"""Concrete extraction strategies — chunkers, member extractors, file
discoverers, and the dependency resolver. Each module implements one
Protocol declared in :mod:`pydocs_mcp.extraction.protocols`.
"""
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
]
