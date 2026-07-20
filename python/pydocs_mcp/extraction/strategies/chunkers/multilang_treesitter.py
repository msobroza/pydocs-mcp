"""MultilangChunker — availability-aware tree-sitter chunker for the T3 code
extension set ``.js .ts .tsx .c .h .rs`` (ADR 0021 T3).

ONE registration per T3 extension (the ``chunker_registry`` raises on duplicate
registration, so T2's text chunker and this one can never both claim ``.rs``).
The tree-sitter dependency is optional: when ``[multilang]`` is installed the
chunker emits STRUCTURAL symbols (functions / classes / structs / …) with real
1-indexed spans; when the extra is ABSENT it degrades INTERNALLY to the same
fixed-line text windows T2 uses, so the file still indexes as searchable text —
plus one structured ``multilang_fallback`` log carrying the install hint. This
is the ``NullVectorStore`` degrade-but-keep-indexing precedent: a background
batch build must not abort over an optional enhancement (evidence-treesitter §6).

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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydocs_mcp.extraction.config import _DEFAULT_TEXT_WINDOW_LINES
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.serialization import _register_chunker
from pydocs_mcp.extraction.strategies.chunkers._shared import (
    _content_hash,
    _dedup_slug,
    _docstring_summary,
    _module_from_doc_path,
    _relpath,
    _slice_lines,
    _slugify,
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

# The one actionable hint an operator sees when structural symbols are missing
# — mirrors ``fast_plaid``'s ``_INSTALL_HINT`` message-quality bar.
_INSTALL_HINT = "pip install 'pydocs-mcp[multilang]'"

# (kind, name, start_line, end_line) for one extracted top-level symbol.
_Symbol = tuple[NodeKind, str, int, int]

# Module-scope caches: a compiled ``Language`` / ``Query`` is reused across
# every file of that extension in a build (evidence: recompiling per call still
# hits ~235 files/s, but reuse is free and the probe recommends it).
# ``_UNAVAILABLE_EXTS`` short-circuits repeated import attempts once the extra
# is known absent; ``_LOGGED_FALLBACK_EXTS`` keeps the structured log to one
# line per extension per process (the "one log per build" intent).
_LANG_CACHE: dict[str, Any] = {}
_QUERY_CACHE: dict[str, Any] = {}
_UNAVAILABLE_EXTS: set[str] = set()
_LOGGED_FALLBACK_EXTS: set[str] = set()


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
    the ``[multilang]`` extra (or the grammar's ABI) is unavailable."""
    cached = _LANG_CACHE.get(ext)
    if cached is not None:
        return cached
    if ext in _UNAVAILABLE_EXTS:
        return None
    language = _import_language(ext)
    if language is None:
        _UNAVAILABLE_EXTS.add(ext)
        return None
    _LANG_CACHE[ext] = language
    return language


def _import_language(ext: str) -> Any | None:
    """Lazily import ``tree_sitter`` + the grammar wheel and build a Language.

    Caught: ``ImportError`` (extra absent) and ``ValueError`` (grammar ABI
    incompatible with the pinned core) — both degrade to the text fallback
    rather than aborting a batch index build.
    """
    grammar_module, accessor = LANGUAGE_SPECS[ext][0], LANGUAGE_SPECS[ext][1]
    try:
        import tree_sitter as ts

        grammar = importlib.import_module(grammar_module)
        return ts.Language(getattr(grammar, accessor)())
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

    kinds = LANGUAGE_SPECS[ext][3]
    parser = ts.Parser(language)
    tree = parser.parse(content.encode("utf-8"))  # Tree bound to a live local
    cursor = ts.QueryCursor(_compiled_query(ext, language))  # cursor too
    symbols: list[_Symbol] = []
    for _pattern, captures in cursor.matches(tree.root_node):
        symbol = _symbol_from_match(captures, kinds)
        if symbol is not None:
            symbols.append(symbol)
    return symbols


def _symbol_from_match(captures: Any, kinds: Any) -> _Symbol | None:
    item = captures.get("item")
    if not item:
        return None
    node = item[0]
    kind = kinds.get(node.type)
    if kind is None:
        return None
    start = node.start_point[0] + 1
    end = node.end_point[0] + 1
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
    valid.sort(key=lambda s: s[2])
    preamble = _slice_lines(lines, 1, valid[0][2] - 1)
    children = _symbol_nodes(valid, lines, module, rel)
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
    symbols: list[_Symbol],
    lines: list[str],
    module: str,
    rel: str,
) -> tuple[DocumentNode, ...]:
    nodes: list[DocumentNode] = []
    seen: dict[str, int] = {}
    for kind, name, start, end in symbols:
        text = _slice_lines(lines, start, end)
        qname = f"{module}.{_dedup_slug(_slugify(name), seen)}"
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
    """Clear the module-scope caches. Test-only seam so the absence path (extra
    blocked via ``sys.modules``) and the present path both run in one process."""
    _LANG_CACHE.clear()
    _QUERY_CACHE.clear()
    _UNAVAILABLE_EXTS.clear()
    _LOGGED_FALLBACK_EXTS.clear()


__all__ = ("MULTILANG_EXTENSIONS", "MultilangChunker")
