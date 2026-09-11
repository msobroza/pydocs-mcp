"""Concrete extraction strategies — chunkers, member extractors, file
discoverers, and the dependency resolver. Each module implements one
Protocol declared in :mod:`pydocs_mcp.extraction.protocols`.
"""

# Imported for its registration side effects, and deliberately HERE, in the
# parent of both ``chunkers`` and ``analyzers``: Python initializes this
# package before any submodule, so every analyzer registers its reference
# queries with the chunker's grammar loadability probe before anything can
# probe a grammar (the index path reaches the grammar fingerprint before it
# imports an analyzer). ``chunkers`` itself must never import ``analyzers`` —
# the dependency points the other way.
from pydocs_mcp.extraction.strategies import analyzers
from pydocs_mcp.extraction.strategies.chunkers import (
    AstPythonChunker,
    HeadingMarkdownChunker,
    NotebookChunker,
)
from pydocs_mcp.extraction.strategies.dependencies import StaticDependencyResolver
from pydocs_mcp.extraction.strategies.discovery import (
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)
from pydocs_mcp.extraction.strategies.members import (
    AstMemberExtractor,
    InspectMemberExtractor,
)

__all__ = [
    "AstMemberExtractor",
    "AstPythonChunker",
    "DependencyFileDiscoverer",
    "HeadingMarkdownChunker",
    "InspectMemberExtractor",
    "NotebookChunker",
    "ProjectFileDiscoverer",
    "StaticDependencyResolver",
    "analyzers",
]
