"""Canonical SQLite factories for the indexing + lookup services.

The MCP CLI (``__main__.py``), the MCP server (``server.py``), the
benchmark suite, and the test suite all construct the same repository
stack around a shared ``ConnectionProvider``. Keeping the composition in one
place means a change to the backend dependencies (e.g. swapping in a
different ``ChunkStore`` implementation) fans out through a single
factory instead of N copies.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import build_connection_provider
from pydocs_mcp.storage.sqlite import (
    SqliteChunkRepository,
    SqliteDocumentTreeStore,
    SqliteModuleMemberRepository,
    SqlitePackageRepository,
    SqliteUnitOfWork,
)

if TYPE_CHECKING:
    from pydocs_mcp.application.lookup_service import LookupService
    from pydocs_mcp.retrieval.config import AppConfig


def build_sqlite_indexing_service(db_path: Path) -> IndexingService:
    """Construct the canonical transactional IndexingService for *db_path*.

    Callers that need the chunk repository directly (e.g. to trigger
    ``rebuild_index`` after a bulk load) can reach it via
    ``service.chunk_store`` — the ``ChunkStore`` protocol exposes it.

    Sub-PR #5: also wires :class:`SqliteDocumentTreeStore` so the
    ``trees`` payload passed to ``reindex_package`` is persisted.
    """
    provider = build_connection_provider(db_path)
    return IndexingService(
        package_store=SqlitePackageRepository(provider=provider),
        chunk_store=SqliteChunkRepository(provider=provider),
        module_member_store=SqliteModuleMemberRepository(provider=provider),
        unit_of_work=SqliteUnitOfWork(provider=provider),
        tree_store=SqliteDocumentTreeStore(provider=provider),
    )


def build_sqlite_lookup_service(
    db_path: Path, config: "AppConfig | None" = None,
) -> "LookupService":
    """Compose a wired LookupService from a SQLite DB path.

    Mirrors :func:`build_sqlite_indexing_service`. The CLI ``lookup``
    subcommand and the MCP server both delegate here for the lookup
    composition so a change to the dependency list fans out through one
    factory. ``ref_svc`` (sub-PR #5b) defaults to None — LookupService
    surfaces its absence to clients as ``ServiceUnavailableError`` for
    the modes that need it (callers/callees).
    """
    from pydocs_mcp.application.lookup_service import LookupService
    from pydocs_mcp.application.package_lookup import PackageLookup
    from pydocs_mcp.application.tree_service import TreeService
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.factories import build_retrieval_context

    cfg = config or AppConfig.load()
    context = build_retrieval_context(db_path, cfg)
    provider = context.connection_provider
    package_lookup = PackageLookup(
        package_store=SqlitePackageRepository(provider=provider),
        chunk_store=SqliteChunkRepository(provider=provider),
        module_member_store=context.module_member_store,
    )
    tree_svc = TreeService(tree_store=SqliteDocumentTreeStore(provider=provider))
    return LookupService(
        package_lookup=package_lookup,
        tree_svc=tree_svc,
        ref_svc=None,  # sub-PR #5b
    )
