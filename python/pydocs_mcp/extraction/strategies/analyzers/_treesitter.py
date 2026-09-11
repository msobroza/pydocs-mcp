"""Shared tree-sitter plumbing for the per-language reference analyzers.

One grammar cache, one degrade path (spec §4.3): grammar availability and
``Language`` objects come from the CHUNKER's caches
(``chunkers/multilang_treesitter.py``), so the declared capability state and
the actual indexing behavior can never disagree within a process. This module
adds only what reference capture needs on top:

- the two capability states of spec §7.2 (D7);
- a compiled-query cache keyed ``(ext, ReferenceQueryRole)`` — the
  calls/inherits/imports query sources differ from the chunker's top-level
  query, so they get their own cache, registered with the chunker's reset
  seam so the degrade tests can flip states in one process;
- the top-level span bisect index that makes attribution joinable by
  construction (spec §4.4): the analyzer runs the CHUNKER's own top-level
  query and assigns qnames with the SAME shared helper the chunker uses;
- the probe-rule query executor (D11): ``QueryCursor.matches()`` only,
  ``Tree`` + cursor bound to live locals across iteration, 1-indexed spans,
  the out-of-range span guard, and empty query → no matches.

Language modules never import ``tree_sitter`` directly — every touch of the
library goes through this module or the chunker's loaders.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import defaultdict, deque
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydocs_mcp.extraction.model import split_newline_rows
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.chunkers._shared import (
    _assign_top_level_qnames,
    _module_from_doc_path,
)
from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import LANGUAGE_SPECS
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _in_range_symbols,
    _load_language,
    _positioned_symbols_from_tree,
    _register_cache_reset,
    _register_probe_queries,
    _tree_point,
)
from pydocs_mcp.extraction.strategies.references import _MAX_TO_NAME_CHARS
from pydocs_mcp.storage.node_reference import NodeReference

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydocs_mcp.extraction.model import NodeKind
    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
        _PositionedSymbol,
        _TreePoint,
    )
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector

    # (start point, end point, qname) — one attribution span of the index.
    _AttributionSpan = tuple[_TreePoint, _TreePoint, str]

    # A language module's import-statement text parser: statement source →
    # (alias entries, IMPORTS targets). Injected, never imported here — the
    # shared plumbing must not depend on any one language's normalizer.
    ImportNormalizer = Callable[[str], tuple[dict[str, str], list[str]]]


class ReferenceQueryRole(StrEnum):
    """Closed vocabulary keying the compiled-query cache and parameterizing
    the shared executor (spec §4.3). Values match the capture-kind strings
    the stage's ``allowed`` frozenset carries."""

    CALLS = "calls"
    INHERITS = "inherits"
    IMPORTS = "imports"


# The two per-deployment capability states (spec §7.2, D7). Module-level
# constants — ``capabilities_for`` returns THESE objects, so identity pins
# (``language_capabilities(ext) is analyzer.capabilities``) stay valid.
TREESITTER_ACTIVE_CAPABILITIES: LanguageCapabilities = {
    "outline": "available",
    "definitions": "available",
    "references": "syntactic",
}
TREESITTER_DEGRADED_CAPABILITIES: LanguageCapabilities = {
    "outline": "available",  # text-window fallback still persists a module tree
    "definitions": "unavailable",  # no symbol nodes in the fallback shape
    "references": "unavailable",  # the analyzer no-ops (D11)
}


def capabilities_for(ext: str) -> LanguageCapabilities:
    """Per-deployment capability state for a tree-sitter extension (D7).

    Routes through the CHUNKER's memoized ``_load_language`` verdict — O(1)
    after first touch, and structurally incapable of disagreeing with what
    indexing actually did (spec §7.2 invariant).

    Raises ``ValueError`` for an extension with no grammar spec: unguarded, the
    lookup surfaced as a bare ``KeyError`` raised deep inside the chunker's
    grammar import, which reads as a crash rather than "wrong caller".
    ``.py`` / ``.md`` have their OWN analyzers and must never route here.
    """
    if ext not in LANGUAGE_SPECS:
        raise ValueError(f"capabilities_for: got {ext!r}, expected one of {sorted(LANGUAGE_SPECS)}")
    active = _load_language(ext) is not None
    return TREESITTER_ACTIVE_CAPABILITIES if active else TREESITTER_DEGRADED_CAPABILITIES


def register_reference_queries(exts: tuple[str, ...], *sources: str) -> None:
    """Join a language module's reference-query sources to the chunker's
    grammar loadability probe, for every extension the module registers.

    Call it at module import, next to the query constants: the probe then
    compiles every query the analyzer runs, so a grammar that rejects one
    degrades the extension as a whole (one memoized verdict, spec §7.2) —
    never a per-file ``QueryError`` after earlier passes emitted rows. Empty
    sources are skipped: an empty query means "no matches" (D11). Example::

        register_reference_queries((".rs",), _RUST_CALLS_QUERY, _RUST_IMPORTS_QUERY)
    """
    non_empty = tuple(source for source in sources if source.strip())
    for ext in exts:
        _register_probe_queries(ext, non_empty)


# Compiled reference queries, keyed (ext, role) — the analyzer-side sibling of
# the chunker's per-ext top-level query cache. Registered with the shared
# reset seam below so degrade tests can flip grammar states in one process.
_REFERENCE_QUERY_CACHE: dict[tuple[str, ReferenceQueryRole], Any] = {}
_register_cache_reset(_REFERENCE_QUERY_CACHE.clear)

# Identifier-chain targets only — the string mirror of ``canonical_dotted``'s
# None policy (references.py): computed callees and punctuation never become
# ``to_name`` rows. ``$`` joins ``\w`` for JavaScript identifiers.
_DOTTED_TARGET_RE = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*")


def canonical_target(raw: str | None) -> str | None:
    """``::`` / ``/`` → ``.``, then keep only clean dotted identifier chains.

    Mirrors ``canonical_dotted``'s drop-don't-guess policy and its
    ``_MAX_TO_NAME_CHARS`` cap + trailing-ellipsis convention (spec §5.1).
    """
    if raw is None:
        return None
    text = raw.replace("::", ".").replace("/", ".").strip()
    if _DOTTED_TARGET_RE.fullmatch(text) is None:
        return None
    if len(text) > _MAX_TO_NAME_CHARS:
        return text[: _MAX_TO_NAME_CHARS - 1] + "…"
    return text


def node_text(node: Any) -> str:
    """Decoded source text of a tree-sitter node (replace-on-error, like the
    chunker's ``_capture_name``)."""
    return str(node.text.decode("utf-8", "replace"))


def add_reference(
    collector: ReferenceCollector,
    *,
    from_package: str,
    from_node_id: str,
    to_name: str | None,
    kind: ReferenceKind,
) -> None:
    """Emit one unresolved candidate; ``to_name=None`` (dropped target) is a
    no-op. ``to_node_id`` stays None — the resolver flips it later (AC-21)."""
    if to_name is None:
        return
    collector.add(
        NodeReference(
            from_package=from_package,
            from_node_id=from_node_id,
            to_name=to_name,
            to_node_id=None,
            kind=kind,
        )
    )


def record_aliases(collector: ReferenceCollector, module: str, aliases: dict[str, str]) -> None:
    """Merge alias entries under the suffix-preserving module qname.

    Empty input is a no-op — no empty dict may be created, because the C
    analyzer pins an EMPTY alias table (includes are not renaming imports,
    AC-19)."""
    if not aliases:
        return
    collector.aliases.setdefault(module, {}).update(aliases)


def capture_named_edges(
    session: CaptureSession,
    role: ReferenceQueryRole,
    query: str,
    *,
    capture_name: str,
    kind: ReferenceKind,
    from_package: str,
    collector: ReferenceCollector,
    skip_names: frozenset[str] = frozenset(),
) -> None:
    """Emit one ``kind`` edge per MATCH, from that match's FIRST
    ``@capture_name`` node.

    The one-edge-per-match shape every language's CALLS and INHERITS pass
    wants, so it lives beside the ``(ext, role)`` query cache instead of
    inside any language module (cross-language private imports are banned).
    Everything language-specific is a parameter: the capture name, the kind,
    and ``skip_names`` — the callee vocabulary another pass already consumed
    (JavaScript / TypeScript ``require``, spec §5.4). Example::

        capture_named_edges(session, ReferenceQueryRole.INHERITS, query,
                            capture_name="parent", kind=ReferenceKind.INHERITS,
                            from_package="pkg", collector=collector)

    Per-match, not per-node, is safe for every multi-parent heritage shape
    shipped so far: tree-sitter yields ONE match per captured parent, even
    when the grammar nests them under a single clause node. Probe evidence
    (tree-sitter 0.25.2 + tree-sitter-java 0.23.5), the densest such shape::

        class A implements I, J    → (super_interfaces (type_list
                                        (type_identifier) (type_identifier)))
                                   → two matches, one parent each

    A future grammar that packs several capture nodes into one match needs
    this loop to iterate ``nodes``, with the skip check moved inside.
    """
    for captures in session.matches(role, query):
        nodes = captures.get(capture_name)
        if not nodes:
            continue
        text = node_text(nodes[0])
        if text in skip_names:
            continue
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(nodes[0]),
            to_name=canonical_target(text),
            kind=kind,
        )


def emit_statement_import(
    session: CaptureSession,
    node: Any,
    *,
    normalize: ImportNormalizer,
    from_package: str,
    collector: ReferenceCollector,
) -> None:
    """Record one import/export statement's aliases and IMPORTS rows.

    The statement TEXT, comments blanked (``_text_without_comments``), is
    parsed by the caller's ``normalize`` — the language module's own text
    normalizer, never named here (any language whose imports are one
    statement node qualifies: ECMAScript modules, Java ``import``
    declarations). This helper owns only the collector protocol::

        emit_statement_import(session, node, normalize=self_language_normalizer,
                              from_package="pkg", collector=collector)
    """
    aliases, targets = normalize(_text_without_comments(node))
    record_aliases(collector, session.module, aliases)
    from_node_id = session.enclosing_qname(node)  # one statement, one attribution
    for target in targets:
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=from_node_id,
            to_name=canonical_target(target),
            kind=ReferenceKind.IMPORTS,
        )


def _text_without_comments(node: Any) -> str:
    """``node``'s source text with every comment inside it blanked to spaces.

    Comments are part of a statement node's text, and the import normalizers
    read the FIRST ``from '…'`` / ``{…}`` they find, so
    ``import { a, // was from 'old'`` fabricated an IMPORTS row and an alias.
    Blanking the grammar's own comment nodes — never a regex on ``//``, which
    would also eat URL specifiers — keeps the real tokens and every byte
    offset intact (one space per byte, so the result stays valid UTF-8).
    """
    raw = bytearray(node.text)
    base = node.start_byte
    for comment in _comment_nodes_under(node):
        start, end = comment.start_byte - base, comment.end_byte - base
        raw[start:end] = b" " * (end - start)
    return raw.decode("utf-8", "replace")


def _comment_nodes_under(node: Any) -> list[Any]:
    """Outermost comment descendants of ``node``. Every shipped grammar names
    them ``comment``, ``line_comment`` or ``block_comment``; a comment's own
    children (Rust doc markers) already sit inside its blanked range."""
    found: list[Any] = []
    stack = list(node.children)
    while stack:
        child = stack.pop()
        if child.type.endswith("comment"):
            found.append(child)
        else:
            stack.extend(child.children)
    return found


def capture_statement_imports(
    session: CaptureSession,
    query: str,
    *,
    normalize: ImportNormalizer,
    from_package: str,
    collector: ReferenceCollector,
) -> None:
    """Run an IMPORTS query whose ``@import`` captures are whole statement
    nodes, recording each statement through ``emit_statement_import``.

    The shared loop for every language whose import is ONE statement node
    (Rust ``use``, Java ``import``, TypeScript import/re-export). JavaScript
    keeps its own per-match dispatch: its query also carries CommonJS
    ``require``. Every ``@import`` node of a match is visited; a statement
    query yields one per match, so this agrees with ``capture_named_edges``'
    first-node rule. Example::

        capture_statement_imports(session, query, normalize=self_language_normalizer,
                                  from_package="pkg", collector=collector)
    """
    for captures in session.matches(ReferenceQueryRole.IMPORTS, query):
        for node in captures.get("import", []):
            emit_statement_import(
                session,
                node,
                normalize=normalize,
                from_package=from_package,
                collector=collector,
            )


class _TopLevelSymbolIndex:
    """Bisect index: tree-sitter point → enclosing top-level symbol qname.

    Positions are tree-sitter ``(row, column)`` points (0-indexed, end
    exclusive), not bare lines: with two top-level items on ONE line
    (minified JS/TS, one-line C) a line bisect handed every capture on that
    line to the later item — a wrong edge, where the contract is "no edge,
    never a wrong edge". Root-anchored chunker queries cannot produce
    overlapping top-level spans (spec §4.4), so bisect on the start point +
    an end-point check suffices. Points outside every span (imports,
    preamble, top-level statements) attribute to the module qname. On
    multi-line code this matches the old line bisect except for a capture
    sitting on a span's first or last line OUTSIDE its columns — exactly
    the captures the line bisect mis-attributed.

    ``spans`` arrive in ``_assign_top_level_qnames`` order, the ONE owner of
    the qname rule. The start-point sort below is only the bisect's own
    precondition. Spans never overlap: root-anchored items are disjoint, and
    a multi-declarator statement (JS ``const a = …, b = …``) contributes one
    span per declarator (the chunker's ``_attribution_node``), so two symbols
    never share one. The sort is stable, so identical spans — which no
    shipped query produces — would still bisect deterministically (the later
    one wins).
    """

    def __init__(self, module: str, spans: list[_AttributionSpan]) -> None:
        self._module = module
        self._spans = sorted(spans, key=lambda span: span[0])
        self._starts = [start for start, _end, _qname in self._spans]

    def enclosing(self, point: _TreePoint) -> str:
        i = bisect_right(self._starts, point) - 1
        if i < 0:
            return self._module
        _start, end, qname = self._spans[i]
        return qname if point < end else self._module


class CaptureSession:
    """One parsed file: module id, live Tree, span index, query runner.

    A plain class, not a frozen dataclass: it must hold the tree-sitter
    ``Tree`` in a live attribute across every query iteration — a GC'd
    temporary segfaults (probe rule, evidence-treesitter §3 / D11).
    """

    def __init__(
        self,
        ext: str,
        language: Any,
        tree: Any,
        module: str,
        index: _TopLevelSymbolIndex,
    ) -> None:
        self._ext = ext
        self.module = module
        self._language = language
        self._tree = tree
        self._index = index

    def matches(self, role: ReferenceQueryRole, query_source: str) -> list[dict[str, Any]]:
        """Probe-rule query runner: ``matches()`` only (never ``captures()``);
        an empty query source is "no matches" without touching tree-sitter
        (D11)."""
        if not query_source.strip():
            return []
        import tree_sitter as ts

        query = _reference_query(self._ext, role, query_source, self._language)
        cursor = ts.QueryCursor(query)  # live local across iteration (probe rule)
        return [captures for _pattern, captures in cursor.matches(self._tree.root_node)]

    def enclosing_qname(self, node: Any) -> str:
        """Attribution (spec §4.4): the captured node's start point, bisected
        against the chunker's own top-level spans — both raw tree-sitter
        points, so no 0/1-index conversion sits between them."""
        return self._index.enclosing(_tree_point(node.start_point))


def open_capture_session(source: str, *, path: str, root: Path) -> CaptureSession | None:
    """Parse ``source`` and build the attribution index for its extension.

    Returns None when the grammar is unavailable — the analyzer no-ops and
    the CHUNKER's one-per-extension ``multilang_fallback`` log stays the
    single operator signal (D11: no second log here).
    """
    ext = Path(path).suffix.lower()
    language = _load_language(ext)
    if language is None:
        return None
    import tree_sitter as ts

    module = _module_from_doc_path(path, root)
    parser = ts.Parser(language)  # named local, like the chunker's _extract_symbols
    tree = parser.parse(source.encode("utf-8"))  # bound live on the session below
    index = _symbol_index(ext, language, tree, source, module)
    return CaptureSession(ext, language, tree, module, index)


def _symbol_index(
    ext: str, language: Any, tree: Any, source: str, module: str
) -> _TopLevelSymbolIndex:
    """Run the CHUNKER's own top-level extraction
    (``_positioned_symbols_from_tree``) over the analyzer's parse — one parse
    per file on the analyzer side, and the compiled top-level query is the
    chunker's own cached object — then assign qnames with the shared helper:
    joinability by construction. Only the attribution spans add the
    ``_attribution_node`` points on top."""
    positioned = _positioned_symbols_from_tree(ext, language, tree)
    symbols = [symbol for symbol, _start, _end in positioned]
    # The chunker's row count — split_newline_rows on both sides, or the "SAME
    # call" claim above would have one differing input (issue #246 item 4).
    valid = _in_range_symbols(symbols, len(split_newline_rows(source)))
    assigned = _assign_top_level_qnames(valid, module)
    return _TopLevelSymbolIndex(module, _attribution_spans(assigned, positioned))


def _attribution_spans(
    assigned: list[tuple[str, NodeKind, str, int, int]],
    positioned: list[_PositionedSymbol],
) -> list[_AttributionSpan]:
    """Pair each assigned qname with its OWN attribution span's start/end
    points (the item node, or its declarator — ``_attribution_node``).

    Keyed ``(kind, name, start_line)`` — ``_in_range_symbols`` may clamp an
    END line, never a start — with one FIFO per key: the shared helper sorts
    stably, so entries sharing a key keep extraction order, the same order
    their ``_N`` dedup suffixes follow. Out-of-range symbols are queued but
    never popped (no assigned entry shares their start line).
    """
    points: defaultdict[tuple[NodeKind, str, int], deque[tuple[_TreePoint, _TreePoint]]]
    points = defaultdict(deque)
    for (kind, name, start, _end), start_point, end_point in positioned:
        points[(kind, name, start)].append((start_point, end_point))
    spans: list[_AttributionSpan] = []
    for qname, kind, name, start, _end in assigned:
        start_point, end_point = points[(kind, name, start)].popleft()
        spans.append((start_point, end_point, qname))
    return spans


def _reference_query(ext: str, role: ReferenceQueryRole, query_source: str, language: Any) -> Any:
    """Compile once per ``(ext, role)``, then reuse the ``Query`` object.

    ``query_source`` is deliberately NOT part of the key: each (extension, role)
    pair has exactly ONE query source, owned by its language module as a module
    constant. Keying on the source string would make the cache unbounded for no
    gain and hide a genuine bug — two different sources arriving for one
    (ext, role) means a language module is generating queries per call.
    """
    key = (ext, role)
    cached = _REFERENCE_QUERY_CACHE.get(key)
    if cached is not None:
        return cached
    import tree_sitter as ts

    query = ts.Query(language, query_source)
    _REFERENCE_QUERY_CACHE[key] = query
    return query


__all__ = (
    "TREESITTER_ACTIVE_CAPABILITIES",
    "TREESITTER_DEGRADED_CAPABILITIES",
    "CaptureSession",
    "ReferenceQueryRole",
    "add_reference",
    "canonical_target",
    "capabilities_for",
    "capture_named_edges",
    "capture_statement_imports",
    "emit_statement_import",
    "node_text",
    "open_capture_session",
    "record_aliases",
    "register_reference_queries",
)
