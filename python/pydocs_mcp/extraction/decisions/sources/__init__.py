"""Concrete decision-mining sources (spec §D8).

One file per source; each class carries
``@decision_source_registry.register("name")`` at module scope, so importing
this package registers all of them. This slice ships ``inline_markers`` and
``adr_files``; ``commit_messages`` / ``changelog`` / ``docs_prose`` land in a
later slice.
"""

from __future__ import annotations

from pydocs_mcp.extraction.decisions.sources.adr_files import AdrFilesSource
from pydocs_mcp.extraction.decisions.sources.inline_markers import InlineMarkersSource

__all__ = ["AdrFilesSource", "InlineMarkersSource"]
