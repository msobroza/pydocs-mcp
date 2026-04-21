"""Canonical SQLite wiring factory for :class:`IndexingService`.

The MCP CLI (``__main__.py``), the benchmark suite, and the test suite
all construct the same four-repository stack around a shared
``ConnectionProvider``. Keeping the wiring in one place means a change
to the backend dependencies (e.g. swapping in a different
``ChunkStore`` implementation) fans out through a single factory
instead of N copies.
"""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import build_connection_provider
from pydocs_mcp.storage.sqlite import (
    SqliteChunkRepository,
    SqliteDocumentTreeStore,
    SqliteModuleMemberRepository,
    SqlitePackageRepository,
    SqliteUnitOfWork,
)


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
