"""Application-layer use-case services (spec §5.1).

Thin orchestration objects composed from Protocol-only constructor
arguments — ``PackageStore`` / ``ChunkStore`` / ``ModuleMemberStore`` on
the storage side, ``CodeRetrieverPipeline`` on the retrieval side.

Write-side bootstrap (:class:`IndexProjectService`) composes with the
strategy classes from :mod:`pydocs_mcp.extraction`
(:class:`PipelineChunkExtractor` / :class:`AstMemberExtractor` /
:class:`InspectMemberExtractor` / :class:`StaticDependencyResolver`). The
query services ship alongside so the CLI (``__main__``) can wire a full
indexing + query stack with one import. Rendering helpers in
:mod:`pydocs_mcp.application.formatting` are the single source of truth for
byte-level output but are imported directly by their consumers.
"""
from __future__ import annotations

from pydocs_mcp.application.document_tree_service import (
    DocumentTreeService,
    NotFoundError,
)
from pydocs_mcp.application.index_project_service import IndexProjectService
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.module_introspection_service import (
    ModuleIntrospectionService,
)
from pydocs_mcp.application.package_lookup_service import PackageLookupService
from pydocs_mcp.application.protocols import (
    ChunkExtractor,
    DependencyResolver,
    MemberExtractor,
)
from pydocs_mcp.application.search_api_service import SearchApiService
from pydocs_mcp.application.search_docs_service import SearchDocsService

__all__ = [
    "ChunkExtractor",
    "DependencyResolver",
    "DocumentTreeService",
    "IndexProjectService",
    "IndexingService",
    "MemberExtractor",
    "ModuleIntrospectionService",
    "NotFoundError",
    "PackageLookupService",
    "SearchApiService",
    "SearchDocsService",
]
