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
  the out-of-range span guard, and empty query → no matches (C inherits).

Language modules never import ``tree_sitter`` directly — every touch of the
library goes through this module or the chunker's loaders.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.chunkers._shared import (
    _assign_top_level_qnames,
    _module_from_doc_path,
)
from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import LANGUAGE_SPECS
from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
    _compiled_query,
    _in_range_symbols,
    _load_language,
    _register_cache_reset,
    _symbol_from_match,
)
from pydocs_mcp.extraction.strategies.references import _MAX_TO_NAME_CHARS
from pydocs_mcp.storage.node_reference import NodeReference

if TYPE_CHECKING:
    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector


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
    """
    active = _load_language(ext) is not None
    return TREESITTER_ACTIVE_CAPABILITIES if active else TREESITTER_DEGRADED_CAPABILITIES


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


class _TopLevelSymbolIndex:
    """Bisect index: 1-indexed line → enclosing top-level symbol qname.

    Root-anchored chunker queries cannot produce overlapping top-level spans
    (spec §4.4), so bisect on start line + an end-line check suffices. Lines
    outside every span (imports, preamble, top-level statements) attribute
    to the module qname.
    """

    def __init__(self, module: str, assigned: list[tuple[str, Any, str, int, int]]) -> None:
        ordered = sorted(assigned, key=lambda item: item[3])
        self._module = module
        self._starts = [start for (_q, _k, _n, start, _e) in ordered]
        self._spans = [(start, end, qname) for (qname, _k, _n, start, end) in ordered]

    def enclosing(self, line: int) -> str:
        i = bisect_right(self._starts, line) - 1
        if i < 0:
            return self._module
        start, end, qname = self._spans[i]
        return qname if start <= line <= end else self._module


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
        self.ext = ext
        self.module = module
        self._language = language
        self._tree = tree
        self._index = index

    def matches(self, role: ReferenceQueryRole, query_source: str) -> list[dict[str, Any]]:
        """Probe-rule query runner: ``matches()`` only (never ``captures()``);
        an empty query source (C inherits) is "no matches" without touching
        tree-sitter (D11)."""
        if not query_source.strip():
            return []
        import tree_sitter as ts

        query = _reference_query(self.ext, role, query_source, self._language)
        cursor = ts.QueryCursor(query)  # live local across iteration (probe rule)
        return [captures for _pattern, captures in cursor.matches(self._tree.root_node)]

    def enclosing_qname(self, node: Any) -> str:
        """Attribution (spec §4.4): the captured node's 1-indexed start line,
        bisected against the chunker's own top-level spans."""
        return self._index.enclosing(node.start_point[0] + 1)


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
    tree = ts.Parser(language).parse(source.encode("utf-8"))  # bound live below
    index = _symbol_index(ext, language, tree, source, module)
    return CaptureSession(ext, language, tree, module, index)


def _symbol_index(
    ext: str, language: Any, tree: Any, source: str, module: str
) -> _TopLevelSymbolIndex:
    """Run the CHUNKER's own top-level query over the analyzer's parse and
    assign qnames with the shared helper — joinability by construction."""
    symbols = _top_level_symbols(ext, language, tree)
    valid = _in_range_symbols(symbols, len(source.splitlines()))
    return _TopLevelSymbolIndex(module, _assign_top_level_qnames(valid, module))


def _top_level_symbols(ext: str, language: Any, tree: Any) -> list[tuple[Any, str, int, int]]:
    """The chunker's ``_extract_symbols`` over an existing tree (one parse per
    file on the analyzer side; the compiled top-level query is the chunker's
    own cached object)."""
    import tree_sitter as ts

    kinds = LANGUAGE_SPECS[ext][3]
    cursor = ts.QueryCursor(_compiled_query(ext, language))  # live local
    symbols: list[tuple[Any, str, int, int]] = []
    for _pattern, captures in cursor.matches(tree.root_node):
        symbol = _symbol_from_match(captures, kinds)
        if symbol is not None:
            symbols.append(symbol)
    return symbols


def _reference_query(ext: str, role: ReferenceQueryRole, query_source: str, language: Any) -> Any:
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
    "node_text",
    "open_capture_session",
    "record_aliases",
)
