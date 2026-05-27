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

from pydocs_mcp.application.api_search import ApiSearch
from pydocs_mcp.application.docs_search import DocsSearch
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
from pydocs_mcp.application.reference_service import ReferenceService
from pydocs_mcp.application.tree_service import TreeService

# NOTE: the extraction Protocols (``ChunkExtractor``, ``MemberExtractor``,
# ``DependencyResolver``, ``ExtractionResult``) are intentionally NOT
# re-exported from this package. They are consumer-side contracts for
# ``extraction/`` (implementors) and the composition roots only;
# importers MUST reach into :mod:`pydocs_mcp.application.protocols`
# directly. This keeps the package-level surface focused on the
# composition-root services and the MCP-public input/error types the
# CLI and MCP handlers need.
__all__ = [
    "ApiSearch",
    "DocsSearch",
    "IndexingService",
    "InvalidArgumentError",
    "LookupInput",
    "LookupService",
    "MCPToolError",
    "ModuleInspector",
    "NotFoundError",
    "PackageLookup",
    "ProjectIndexer",
    "ReferenceService",
    "SearchInput",
    "ServiceUnavailableError",
    "TreeService",
]
