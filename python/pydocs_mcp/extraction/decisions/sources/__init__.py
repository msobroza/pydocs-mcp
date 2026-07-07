"""Concrete decision-mining sources (spec §D8).

One file per source; each class carries
``@decision_source_registry.register("name")`` at module scope, so importing
this package registers all of them. Ships the five deterministic sources:
``inline_markers`` + ``adr_files`` (tree/file structured), plus
``commit_messages`` + ``changelog`` + ``docs_prose`` (git/prose keyword-gated).
"""

from __future__ import annotations

from pydocs_mcp.extraction.decisions.sources.adr_files import AdrFilesSource
from pydocs_mcp.extraction.decisions.sources.changelog import ChangelogSource
from pydocs_mcp.extraction.decisions.sources.commit_messages import CommitMessagesSource
from pydocs_mcp.extraction.decisions.sources.docs_prose import DocsProseSource
from pydocs_mcp.extraction.decisions.sources.inline_markers import InlineMarkersSource

__all__ = [
    "AdrFilesSource",
    "ChangelogSource",
    "CommitMessagesSource",
    "DocsProseSource",
    "InlineMarkersSource",
]
