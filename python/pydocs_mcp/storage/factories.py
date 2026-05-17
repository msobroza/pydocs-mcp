"""Canonical SQLite factories for the indexing + lookup services (post-#5a-2).

The MCP CLI (``__main__.py``), the MCP server (``server.py``), the
benchmark suite, and the test suite all construct services around a
shared ``ConnectionProvider`` + ``SqliteUnitOfWork`` factory. Keeping the
composition in one place means a change to the backend dependencies
(e.g. swapping in a different ``UnitOfWork`` implementation) fans out
through a single factory instead of N copies.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import build_connection_provider
from pydocs_mcp.storage.sqlite import SqliteUnitOfWork

if TYPE_CHECKING:
    from pydocs_mcp.application.lookup_service import LookupService
    from pydocs_mcp.retrieval.config import AppConfig


def build_sqlite_uow_factory(db_path: Path) -> Callable[[], SqliteUnitOfWork]:
    """Build a fresh-per-call ``SqliteUnitOfWork`` factory bound to a single
    ``ConnectionProvider``.

    Each call to the returned callable instantiates a NEW ``SqliteUnitOfWork``
    — instances are not reusable (the re-entrance guard fires). The provider
    is captured by closure once at factory-construction time so all UoWs
    share the same connection-pool semantics.
    """
    provider = build_connection_provider(db_path)
    return lambda: SqliteUnitOfWork(provider=provider)


def build_sqlite_indexing_service(db_path: Path) -> IndexingService:
    """Construct the canonical transactional IndexingService for *db_path*.

    Post-#5a-2: ``IndexingService`` depends on a single ``uow_factory``
    callable. Each public-method call opens a fresh UoW, runs its write
    sequence, and commits — no more "5 stores + optional UoW" wiring.
    """
    return IndexingService(uow_factory=build_sqlite_uow_factory(db_path))


def build_sqlite_lookup_service(
    db_path: Path, config: "AppConfig | None" = None,  # noqa: ARG001 -- kept for API stability
) -> "LookupService":
    """Compose a wired LookupService from a SQLite DB path.

    Post-#5a-2: ``PackageLookup`` and ``TreeService`` each depend on a
    ``uow_factory``. We build ONE factory and thread it through both so
    they share connection-pool semantics. ``ref_svc`` (sub-PR #5b) defaults
    to None — LookupService surfaces its absence to clients as
    ``ServiceUnavailableError`` for the modes that need it.
    """
    from pydocs_mcp.application.lookup_service import LookupService
    from pydocs_mcp.application.package_lookup import PackageLookup
    from pydocs_mcp.application.tree_service import TreeService

    uow_factory = build_sqlite_uow_factory(db_path)
    package_lookup = PackageLookup(uow_factory=uow_factory)
    tree_svc = TreeService(uow_factory=uow_factory)
    return LookupService(
        package_lookup=package_lookup,
        tree_svc=tree_svc,
        ref_svc=None,  # sub-PR #5b
    )
