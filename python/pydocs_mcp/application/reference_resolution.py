"""The declared reference-resolution level of a ``get_references`` answer.

One concept, one seam: the analyzer registry says which languages carry a
reference analyzer at all, and the ANSWERING bundle's index-time grammar stamp
says whether a tree-sitter language's graph was ever captured (ADR 0022;
issue #246 item 3). ``ToolRouter`` imports this function alone instead of the
three seams behind it.
"""

from __future__ import annotations

from pydocs_mcp.extraction.strategies.analyzers import (
    TREESITTER_ACTIVE_CAPABILITIES,
    language_capabilities,
)

# The stamp's vocabulary is the chunker's extension set — the tuple
# `loadable_grammar_fingerprint` iterates — so "does the stamp speak for this
# extension" is decided against it, not against capability-object identity.
from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import MULTILANG_EXTENSIONS
from pydocs_mcp.storage.index_metadata import IndexMetadata

# meta.resolution value when the target's extension carries no registered
# analyzer, or when the bundle's index-time grammar stamp does not cover it;
# the §5.1 LanguageCapabilities vocabulary admits it. The router never
# overstates a structurally empty graph (ADR 0022).
_UNAVAILABLE_RESOLUTION = "unavailable"


def declared_reference_resolution(ext: str | None, metadata: IndexMetadata) -> str:
    """Declared reference-resolution level for a target with extension ``ext``,
    served from the bundle ``metadata`` stamps.

    The seven tree-sitter extensions answer from the INDEX, not the serving
    process (ADR 0022 follow-up, issue #246 item 3): the graph rows were
    written at index time or never, so what decides "syntactic" is whether the
    grammar loaded THEN — the bundle's ``loadable_grammars`` stamp — and the
    value declared is the one the analyzer declares when active. A process that
    can load grammars serving a bundle built without them says "unavailable"
    over the empty graph; a process that cannot load them still serves the rows
    a stamped bundle holds. An unstamped bundle (pre-stamp) declines the claim.
    Decided BEFORE the registry is consulted: an analyzer's live capability
    verdict probes the grammar in the serving process, a cost paid for a value
    this function would ignore.

    ``.py`` and ``.md`` always declare "syntactic" through the analyzer
    registry (ADR 0021 Decision 6). Text/config extensions and targets with no
    resolvable extension carry no analyzer → ``language_capabilities`` returns
    None → "unavailable". Example::

        declared_reference_resolution(".rs", metadata)  # "syntactic" iff stamped
    """
    if not ext:
        return _UNAVAILABLE_RESOLUTION
    if ext in MULTILANG_EXTENSIONS:
        if metadata.grammar_loaded(ext):
            return TREESITTER_ACTIVE_CAPABILITIES["references"]
        return _UNAVAILABLE_RESOLUTION
    caps = language_capabilities(ext)
    return caps["references"] if caps is not None else _UNAVAILABLE_RESOLUTION
