"""MultilangChunker — availability-aware tree-sitter chunker for the T3 code
extension set ``MULTILANG_EXTENSIONS`` (ADR 0021 T3 / ADR 0022).

ONE registration per T3 extension (the ``chunker_registry`` raises on duplicate
registration, so T2's text chunker and this one can never both claim ``.rs``).
The tree-sitter core and grammar wheels ship in the required runtime deps
(ADR 0022; formerly the multilang extra). When a grammar loads, the chunker
emits STRUCTURAL symbols (functions / classes / structs / …) with real
1-indexed spans; when it does not (wheel-less sdist install, grammar/core ABI
mismatch) the chunker degrades INTERNALLY to the same fixed-line text windows
T2 uses, so the file still indexes as searchable text — plus one structured
``multilang_fallback`` log carrying a reinstall-from-wheels hint. This is the
``NullVectorStore`` degrade-but-keep-indexing precedent: a background batch
build must not abort over one unloadable grammar (evidence-treesitter §6).

Probe-derived tree-sitter rules (evidence-treesitter §3, all encoded below):

- use ``QueryCursor.matches()`` — ``captures()`` returns per-name lists in
  independent document order, silently misaligning ``@item`` / ``@name``;
- bind the ``Tree`` AND ``QueryCursor`` to live locals across the whole
  iteration — an inline temporary is GC'd mid-iteration and segfaults;
- map spans as ``start_point.row + 1 .. end_point.row + 1`` (1-indexed);
- reuse compiled ``Query`` objects per language (cached here at module scope);
- pin ``tree-sitter>=0.25,<0.26`` — 0.26.0 has a use-after-free in
  ``matches()``. The out-of-range span guard below is the CI canary: a future
  bad core release that returns the ``0x3FFFFFFE`` invalid-node sentinel row
  is dropped rather than emitted as a garbage span.
"""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydocs_mcp.extraction.config import _DEFAULT_TEXT_WINDOW_LINES
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.serialization import _register_chunker
from pydocs_mcp.extraction.strategies.chunkers._shared import (
    _assign_top_level_qnames,
    _content_hash,
    _docstring_summary,
    _module_from_doc_path,
    _relpath,
    _slice_lines,
)
from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import (
    LANGUAGE_SPECS,
    MULTILANG_EXTENSIONS,
)
from pydocs_mcp.extraction.strategies.chunkers.text_section import (
    _module_node,
    _window_nodes,
)

if TYPE_CHECKING:
    from pydocs_mcp.extraction.config import ChunkingConfig

log = logging.getLogger("pydocs-mcp")

# The one actionable hint an operator sees when structural symbols are
# missing. It names a reinstall, not the multilang extra: that extra is an
# empty no-op alias since the wheels became required deps (spec §6.2). The
# degrade causes it fixes are listed on ``_import_language``. The restart is
# part of the fix, not a courtesy: grammar verdicts are memoized per process
# (``loadable_grammar_fingerprint``), so a running server keeps its verdict.
_INSTALL_HINT = (
    "reinstall pydocs-mcp from wheels (grammar unavailable or ABI-mismatched), "
    "then restart the server"
)

# (kind, name, start_line, end_line) for one extracted top-level symbol.
_Symbol = tuple[NodeKind, str, int, int]

# A tree-sitter ``(row, column)`` point, both 0-indexed; end points are
# exclusive. ``_PositionedSymbol`` carries a symbol plus its attribution
# span's start/end points (``_attribution_node``): the chunker needs only the
# symbol, while the analyzers' attribution index needs the columns to tell
# apart top-level items that share one line (minified JS/TS, one-line C).
_TreePoint = tuple[int, int]
_PositionedSymbol = tuple[_Symbol, _TreePoint, _TreePoint]

# Item types whose ONE statement can name several top-level symbols
# (`const a = …, b = …`, terser `join_vars` output). Their attribution span is
# the symbol's own declarator, not the shared statement: with the statement's
# points every capture in it bisected to the LAST declarator — a wrong edge.
# Attribution only: the chunker keeps the statement's line span (``_Symbol``).
_PER_DECLARATOR_ITEM_TYPES = frozenset({"lexical_declaration"})

# Module-scope caches: a compiled ``Language`` / ``Query`` is reused across
# every file of that extension in a build (evidence: recompiling per call still
# hits ~235 files/s, but reuse is free and the probe recommends it).
# ``_UNAVAILABLE_EXTS`` short-circuits repeated import attempts once a grammar
# is known unloadable; ``_LOGGED_FALLBACK_EXTS`` keeps the structured log to one
# line per extension per process (the "one log per build" intent).
_LANG_CACHE: dict[str, Any] = {}
_QUERY_CACHE: dict[str, Any] = {}
_UNAVAILABLE_EXTS: set[str] = set()
_LOGGED_FALLBACK_EXTS: set[str] = set()

# Sibling tree-sitter caches (the analyzers' compiled reference-query cache)
# register their clear function here so the ONE test seam resets everything.
# A callback list — not a chunkers→analyzers import, which would invert the
# layering (analyzers depend on chunkers, never the reverse).
_EXTRA_CACHE_RESETS: list[Callable[[], None]] = []


def _register_cache_reset(reset: Callable[[], None]) -> None:
    """Join a sibling cache to `_reset_multilang_caches` (analyzers seam)."""
    _EXTRA_CACHE_RESETS.append(reset)


# Reference-query sources the analyzers run, per extension. Each language
# module registers its calls / inherits / imports sources at import
# (``analyzers/_treesitter.register_reference_queries``), so
# ``_import_language`` compiles them inside the ONE loadability probe: a
# grammar that rejects any of them degrades the whole extension, like a
# rejected top-level query, instead of raising per file after earlier passes
# emitted rows — a partial graph whose salt would never flip. A registry, not
# a chunkers→analyzers import, for the layering reason above; the parent
# ``extraction.strategies`` package imports the analyzers, so every
# registration precedes the first probe.
_REFERENCE_PROBE_QUERIES: dict[str, tuple[str, ...]] = {}


def _register_probe_queries(ext: str, sources: tuple[str, ...]) -> None:
    """Add reference-query ``sources`` to ``ext``'s loadability probe.

    Raises ``ValueError`` for an extension with no grammar spec, and
    ``RuntimeError`` once ``ext``'s verdict is memoized: the probe would never
    compile a late source, silently reopening the stranded state. Example::

        _register_probe_queries(".rs", ("(use_declaration) @import",))
    """
    if ext not in LANGUAGE_SPECS:
        raise ValueError(
            f"_register_probe_queries: got {ext!r}, expected one of {sorted(LANGUAGE_SPECS)}"
        )
    if ext in _LANG_CACHE or ext in _UNAVAILABLE_EXTS:
        raise RuntimeError(
            f"_register_probe_queries: the grammar verdict for {ext!r} is already "
            "memoized; register reference queries at analyzer import, before any probe"
        )
    known = _REFERENCE_PROBE_QUERIES.get(ext, ())
    _REFERENCE_PROBE_QUERIES[ext] = known + tuple(s for s in sources if s not in known)


@_register_chunker(".java")
@_register_chunker(".rs")
@_register_chunker(".c")
@_register_chunker(".h")
@_register_chunker(".js")
@_register_chunker(".ts")
@_register_chunker(".tsx")
@dataclass(frozen=True, slots=True)
class MultilangChunker:
    """Tree-sitter symbol chunker with an internal text-window fallback."""

    window_lines: int = _DEFAULT_TEXT_WINDOW_LINES

    @classmethod
    def from_config(cls, cfg: ChunkingConfig) -> MultilangChunker:
        # Reuse T2's window size — the fallback IS T2's windowing.
        return cls(window_lines=cfg.text_section.window_lines)

    def build_tree(
        self,
        path: str,
        content: str,
        package: str,
        root: Path,
    ) -> DocumentNode:
        ext = Path(path).suffix.lower()
        language = _load_language(ext)
        if language is None:
            _log_fallback_once(ext)
            return self._text_fallback(path, content, root)
        tree = _try_symbol_tree(language, ext, path, content, root)
        return tree if tree is not None else self._text_fallback(path, content, root)

    def _text_fallback(self, path: str, content: str, root: Path) -> DocumentNode:
        """T2 fixed-line windows — the file still indexes as searchable text."""
        module = _module_from_doc_path(path, root)
        rel = _relpath(path, root)
        lines = content.splitlines()
        if not lines:
            return _module_node(module, rel, content, direct_text=content, children=())
        children = _window_nodes(lines, module, rel, self.window_lines)
        return _module_node(module, rel, content, direct_text="", children=children)


def _load_language(ext: str) -> Any | None:
    """Return a compiled tree-sitter ``Language`` for ``ext``, or ``None`` when
    the grammar cannot load (causes: ``_import_language``); both outcomes are
    cached so each extension is attempted once per process."""
    cached = _LANG_CACHE.get(ext)
    if cached is not None:
        return cached
    if ext in _UNAVAILABLE_EXTS:
        return None
    loaded = _import_language(ext)
    if loaded is None:
        _UNAVAILABLE_EXTS.add(ext)
        return None
    language, top_level_query = loaded
    _LANG_CACHE[ext] = language
    _QUERY_CACHE[ext] = top_level_query  # the probe's compile IS the chunker's cached query
    return language


def loadable_grammar_fingerprint() -> str:
    """Sorted CSV of the T3 extensions whose grammar loads (analyzers spec §8.2).

    Probes only, no parses: ``_load_language`` memoizes success AND failure,
    so after first touch this costs one dict lookup per extension. The value
    reflects grammar availability at index time, which is exactly what decides
    whether reference capture ran. That is why it salts the package content
    hash: a package indexed while grammars were unloadable must re-extract once
    they load, instead of skipping as cached with an empty graph (D9).

    The ``_load_language`` memo is deliberately SHARED with ``build_tree`` and
    the analyzers' ``capabilities_for`` / capture session, so this salt can
    never disagree with what extraction did in the same process. Re-probing
    past the memo would be worse, not fresher: a grammar repaired mid-process
    would stamp a "grammars load" hash onto a graph the stale chunker verdict
    extracted empty (the stranded state D9 exists to prevent), and that hash
    would then survive a restart. The cost is process-lifetime caching: a
    ``serve --watch`` process sees a fixed grammar only after a restart.

    Example: ``".c,.h,.java,.js,.rs,.ts,.tsx"`` with every grammar wheel
    installed; ``""`` when ``tree_sitter`` itself cannot import.
    """
    loadable = (ext for ext in MULTILANG_EXTENSIONS if _load_language(ext) is not None)
    return ",".join(sorted(loadable))


def _import_language(ext: str) -> tuple[Any, Any] | None:
    """Lazily import ``tree_sitter`` + the grammar wheel, build a Language and
    compile the extension's top-level symbol query → ``(language, query)``,
    plus every reference query its analyzer registered
    (``_REFERENCE_PROBE_QUERIES``).

    Caught: ``ImportError`` (core or grammar wheel absent, e.g. a wheel-less
    sdist install) and ``ValueError`` — the grammar ABI is incompatible with
    the pinned core, OR the grammar loads but rejects the top-level query or
    a reference query (``tree_sitter.QueryError`` subclasses ``ValueError``;
    e.g. a grammar release renaming a node type). All degrade to the text
    fallback rather than aborting a batch index build. The query compiles
    live in THIS probe so a query-incompatible grammar counts as unloadable
    everywhere the shared ``_load_language`` verdict is read: capabilities
    report "unavailable", the analyzer no-ops (no partial graph), and
    ``loadable_grammar_fingerprint`` drops the extension, so the package salt
    flips once the grammar is fixed.
    """
    grammar_module, accessor, query_source, _kinds = LANGUAGE_SPECS[ext]
    try:
        import tree_sitter as ts

        grammar = importlib.import_module(grammar_module)
        language = ts.Language(getattr(grammar, accessor)())
        top_level_query = ts.Query(language, query_source)
        for reference_source in _REFERENCE_PROBE_QUERIES.get(ext, ()):
            # Verdict only — the analyzers compile and cache their own copy
            # (a few extra compiles per extension per process).
            ts.Query(language, reference_source)
        return language, top_level_query
    except (ImportError, ValueError):
        return None


def _compiled_query(ext: str, language: Any) -> Any:
    query = _QUERY_CACHE.get(ext)
    if query is not None:
        return query
    import tree_sitter as ts

    query = ts.Query(language, LANGUAGE_SPECS[ext][2])
    _QUERY_CACHE[ext] = query
    return query


def _try_symbol_tree(
    language: Any,
    ext: str,
    path: str,
    content: str,
    root: Path,
) -> DocumentNode | None:
    """Structural tree, or ``None`` to signal the caller to fall back to text
    (parse/query failure, or zero top-level items — e.g. a data-only file)."""
    try:
        symbols = _extract_symbols(language, ext, content)
    except Exception as exc:  # any tree-sitter failure — degrade to text
        log.warning("multilang parse failed for %s: %s", ext, exc)
        return None
    return _build_symbol_tree(path, content, root, symbols)


def _extract_symbols(language: Any, ext: str, content: str) -> list[_Symbol]:
    import tree_sitter as ts

    parser = ts.Parser(language)
    tree = parser.parse(content.encode("utf-8"))  # Tree bound to a live local
    return _symbols_from_tree(ext, language, tree)


def _symbols_from_tree(ext: str, language: Any, tree: Any) -> list[_Symbol]:
    """Top-level symbols of an already-parsed tree — the chunker's view of
    ``_positioned_symbols_from_tree`` (points dropped). The caller keeps
    ``tree`` referenced across the call."""
    return [symbol for symbol, _start, _end in _positioned_symbols_from_tree(ext, language, tree)]


def _positioned_symbols_from_tree(ext: str, language: Any, tree: Any) -> list[_PositionedSymbol]:
    """Top-level symbols plus their attribution spans' points — the ONE
    extraction loop shared with the analyzers' attribution index
    (``analyzers/_treesitter.py``), so both sides read spans from the same
    cached top-level query (multilang spec §4.4). The caller keeps ``tree``
    referenced across the call."""
    import tree_sitter as ts

    kinds = LANGUAGE_SPECS[ext][3]
    cursor = ts.QueryCursor(_compiled_query(ext, language))  # cursor bound to a live local
    positioned: list[_PositionedSymbol] = []
    for _pattern, captures in cursor.matches(tree.root_node):
        symbol = _symbol_from_match(captures, kinds)
        if symbol is not None:
            span = _attribution_node(captures)
            positioned.append((symbol, _tree_point(span.start_point), _tree_point(span.end_point)))
    return positioned


def _span_node(captures: Any) -> Any:
    """The node whose rows and points bound one symbol: the ``@wrapper``
    (an ``export_statement``) when the query captured one, else ``@item``.

    The wrapper carries what sits between ``export`` and the declaration —
    a decorator written above ``export`` hangs on the statement in both the
    JS and TS grammars, and ``export default`` may take a row of its own.
    Spanning the inner declaration alone dropped those rows from every chunk
    (issue #246 item 1 review): the Angular / NestJS ``@Component(...)``
    metadata vanished from search, and a call in its arguments attributed to
    the module instead of the class the bare form attributes it to.
    """
    wrapper = captures.get("wrapper")
    return wrapper[0] if wrapper else captures["item"][0]


def _attribution_node(captures: Any) -> Any:
    """The node whose points bound one symbol's attribution span: the @name
    node's own ``variable_declarator`` for a multi-declarator statement
    (``_PER_DECLARATOR_ITEM_TYPES``), else the symbol's span node."""
    item = captures["item"][0]
    names = captures.get("name")
    if item.type in _PER_DECLARATOR_ITEM_TYPES and names:
        return names[0].parent
    return _span_node(captures)


def _tree_point(point: Any) -> _TreePoint:
    """A plain ``(row, column)`` tuple from a tree-sitter ``Point``."""
    return (point[0], point[1])


def _symbol_from_match(captures: Any, kinds: Any) -> _Symbol | None:
    item = captures.get("item")
    if not item:
        return None
    node = item[0]
    kind = kinds.get(node.type)
    if kind is None:
        return None
    span = _span_node(captures)
    start = span.start_point[0] + 1
    end = span.end_point[0] + 1
    return (kind, _capture_name(captures), start, end)


def _capture_name(captures: Any) -> str:
    name = captures.get("name")
    if not name:
        return ""
    return str(name[0].text.decode("utf-8", "replace"))


def _build_symbol_tree(
    path: str,
    content: str,
    root: Path,
    symbols: list[_Symbol],
) -> DocumentNode | None:
    module = _module_from_doc_path(path, root)
    rel = _relpath(path, root)
    lines = content.splitlines()
    valid = _in_range_symbols(symbols, len(lines))
    if not valid:
        return None  # no top-level items — caller falls back to windows
    # Shared span→qname assignment (multilang spec §4.4): the analyzers build
    # their attribution index from the SAME call, so edges join this tree by
    # construction.
    assigned = _assign_top_level_qnames(valid, module)
    preamble = _slice_lines(lines, 1, assigned[0][3] - 1)
    children = _symbol_nodes(assigned, lines, rel=rel, module=module)
    return _module_node(module, rel, content, direct_text=preamble, children=children)


def _in_range_symbols(symbols: list[_Symbol], n_lines: int) -> list[_Symbol]:
    """Drop garbage spans (the CI canary against a bad tree-sitter release that
    returns the ``0x3FFFFFFE`` invalid-node sentinel row); clamp ends to EOF."""
    out: list[_Symbol] = []
    for kind, name, start, end in symbols:
        if 1 <= start <= n_lines:
            out.append((kind, name, start, min(end, n_lines)))
    return out


def _symbol_nodes(
    assigned: list[tuple[str, NodeKind, str, int, int]],
    lines: list[str],
    *,
    rel: str,
    module: str,
) -> tuple[DocumentNode, ...]:
    """One symbol DocumentNode per assigned span. ``rel`` / ``module`` are
    keyword-only: both are ``str``, so a positional swap would type-check and
    silently mis-key every node."""
    nodes: list[DocumentNode] = []
    for qname, kind, name, start, end in assigned:
        text = _slice_lines(lines, start, end)
        nodes.append(_symbol_node(qname, name, kind, rel, start, end, text, module))
    return tuple(nodes)


def _symbol_node(
    qname: str,
    name: str,
    kind: NodeKind,
    rel: str,
    start: int,
    end: int,
    text: str,
    module: str,
) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=name,
        kind=kind,
        source_path=rel,
        start_line=start,
        end_line=end,
        text=text,
        content_hash=_content_hash(text, kind, name),
        summary=_docstring_summary(text),
        extra_metadata={"module": module},
        parent_id=module,
    )


def _log_fallback_once(ext: str) -> None:
    """One structured JSON ``multilang_fallback`` log per extension per process
    — the operator learns why symbols are missing without the run breaking."""
    if ext in _LOGGED_FALLBACK_EXTS:
        return
    _LOGGED_FALLBACK_EXTS.add(ext)
    log.warning(
        json.dumps(
            {
                "event": "multilang_fallback",
                "reason": "tree_sitter_unavailable",
                "extension": ext,
                "hint": _INSTALL_HINT,
            }
        )
    )


def _reset_multilang_caches() -> None:
    """Clear the module-scope caches. Test-only seam so the grammar-unavailable
    paths and the present path all run in one process. Two unavailable shapes
    use it: ``tree_sitter`` itself blocked via ``sys.modules`` (every extension
    degrades), or ONE grammar wheel blocked the same way, e.g.
    ``sys.modules["tree_sitter_rust"] = None`` (only ``.rs`` degrades). Also
    clears every sibling cache joined via ``_register_cache_reset``."""
    _LANG_CACHE.clear()
    _QUERY_CACHE.clear()
    _UNAVAILABLE_EXTS.clear()
    _LOGGED_FALLBACK_EXTS.clear()
    for reset in _EXTRA_CACHE_RESETS:
        reset()


__all__ = ("MULTILANG_EXTENSIONS", "MultilangChunker", "loadable_grammar_fingerprint")
