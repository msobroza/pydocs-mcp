"""Application-layer use-case services (spec §5.1).

Thin orchestration objects composed from Protocol-only constructor
arguments — ``PackageStore`` / ``ChunkStore`` / ``ModuleMemberStore`` on
the storage side, ``CodeRetrieverPipeline`` on the retrieval side.

Write-side bootstrap (:class:`ProjectIndexer`) composes with the
strategy classes from :mod:`pydocs_mcp.extraction`
(:class:`PipelineChunkExtractor` / :class:`AstMemberExtractor` /
:class:`InspectMemberExtractor` / :class:`StaticDependencyResolver`). The
query services ship alongside so the CLI (``__main__``) can wire a full
indexing + query stack with one import. Rendering helpers in
:mod:`pydocs_mcp.application.formatting` are the single source of truth for
byte-level output but are imported directly by their consumers.
"""
from __future__ import annotations

from pydocs_mcp.application.docs_search import DocsSearch
from pydocs_mcp.application.document_tree_service import (
    DocumentTreeService,
    NotFoundError,
)
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    MCPToolError,
    NotFoundError,
    ServiceUnavailableError,
)
from pydocs_mcp.application.mcp_inputs import LookupInput, SearchInput
from pydocs_mcp.application.module_inspector import ModuleInspector
from pydocs_mcp.application.package_lookup import PackageLookup
from pydocs_mcp.application.project_indexer import ProjectIndexer
from pydocs_mcp.application.protocols import (
    ChunkExtractor,
    DependencyResolver,
    MemberExtractor,
)
from pydocs_mcp.application.search_api_service import SearchApiService

__all__ = [
    "ChunkExtractor",
    "DependencyResolver",
    "DocsSearch",
    "DocumentTreeService",
    "IndexingService",
    "InvalidArgumentError",
    "LookupInput",
    "LookupService",
    "MCPToolError",
    "MemberExtractor",
    "ModuleInspector",
    "NotFoundError",
    "PackageLookup",
    "ProjectIndexer",
    "SearchApiService",
    "SearchInput",
    "ServiceUnavailableError",
]
