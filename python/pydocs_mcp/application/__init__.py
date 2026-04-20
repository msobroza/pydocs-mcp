"""Application-layer use-case services (spec §5.1).

Thin orchestration objects composed from Protocol-only constructor
arguments — ``PackageStore`` / ``ChunkStore`` / ``ModuleMemberStore`` on
the storage side, ``CodeRetrieverPipeline`` on the retrieval side.

The package body intentionally re-exports only the use-case services;
rendering helpers in :mod:`pydocs_mcp.application.formatting` are the
single source of truth for byte-level output but are imported directly
by their consumers.
"""
from __future__ import annotations

from pydocs_mcp.application.module_introspection_service import (
    ModuleIntrospectionService,
)
from pydocs_mcp.application.package_lookup_service import PackageLookupService
from pydocs_mcp.application.search_api_service import SearchApiService
from pydocs_mcp.application.search_docs_service import SearchDocsService

__all__ = (
    "ModuleIntrospectionService",
    "PackageLookupService",
    "SearchApiService",
    "SearchDocsService",
)
