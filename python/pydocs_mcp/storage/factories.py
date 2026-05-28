"""Canonical SQLite factories for the indexing + lookup services (post-#5a-2).

The MCP CLI (``__main__.py``), the MCP server (``server.py``), the
benchmark suite, and the test suite all construct services around a
shared ``ConnectionProvider`` + ``SqliteUnitOfWork`` factory. Keeping the
composition in one place means a change to the backend dependencies
(e.g. swapping in a different ``UnitOfWork`` implementation) fans out
through a single factory instead of N copies.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import build_connection_provider
from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.filters import Filter
from pydocs_mcp.storage.sqlite import (
    CHUNK_COLUMNS,
    SqliteUnitOfWork,
    _maybe_acquire,
    _SqliteFilterTranslator,
    row_to_chunk,
)
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

logger = logging.getLogger(__name__)

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
    db_path: Path,
    config: AppConfig | None = None,
) -> LookupService:
    """Compose a wired LookupService from a SQLite DB path.

    Post-#5a-2: ``PackageLookup``, ``TreeService``, and ``ReferenceService``
    each depend on a ``uow_factory``. We build ONE factory and thread it
    through all three so they share connection-pool semantics. Post-#5c
    (Task 8): ``ref_svc`` is now a real ``ReferenceService`` instead of
    ``None`` — ``lookup(target=X, show="callers"|"callees")`` resolves
    end-to-end through the reference graph.
    """
    from pydocs_mcp.application.lookup_service import LookupService
    from pydocs_mcp.application.package_lookup import PackageLookup
    from pydocs_mcp.application.reference_service import ReferenceService
    from pydocs_mcp.application.tree_service import TreeService

    uow_factory = build_sqlite_uow_factory(db_path)
    package_lookup = PackageLookup(uow_factory=uow_factory)
    tree_svc = TreeService(uow_factory=uow_factory)
    ref_svc = ReferenceService(uow_factory=uow_factory)
    return LookupService(
        package_lookup=package_lookup,
        tree_svc=tree_svc,
        ref_svc=ref_svc,
    )


def build_composite_uow_factory(
    children: Sequence[Callable[[], object]],
) -> Callable[[], CompositeUnitOfWork]:
    """Wrap N child UoW factories into a composite factory (spec §5.7).

    The returned callable instantiates each child via its factory and
    wraps them in a CompositeUnitOfWork. Order-preserving (children[0]
    commits first; rollback walks in reverse).
    """

    def _make() -> CompositeUnitOfWork:
        return CompositeUnitOfWork(*(f() for f in children))

    return _make


def build_sqlite_plus_turboquant_uow_factory(
    *,
    db_path: Path,
    tq_path: Path,
    dim: int,
    bit_width: int = 4,
) -> Callable[[], CompositeUnitOfWork]:
    """The production composite for pydocs-mcp: SQLite + TurboQuant.

    Used by the composition roots in server.py + __main__.py. Drop-in
    replacement for build_sqlite_uow_factory once dense search is on.
    """
    sqlite_factory = build_sqlite_uow_factory(db_path)
    tq_factory = lambda: TurboQuantUnitOfWork(  # noqa: E731
        index_path=tq_path,
        dim=dim,
        bit_width=bit_width,
    )
    return build_composite_uow_factory([sqlite_factory, tq_factory])


def build_sqlite_candidate_id_resolver(
    db_path: Path,
) -> Callable[[Filter], Awaitable[np.ndarray]]:
    """Build a CandidateIdResolver — runs the filter as SQL against the
    SQLite cache and returns matching chunk IDs as ``np.uint64``.

    Used by ``TurboQuantVectorStore`` to construct its allowlist (spec §7
    risk row 1). The vector store does not import sqlite3 directly; this
    callable is the only seam through which it learns about the relational
    cache, so a future Qdrant / Postgres adapter slots its own resolver in
    without touching the store class.
    """
    provider = build_connection_provider(db_path)
    adapter = _SqliteFilterTranslator(safe_columns=CHUNK_COLUMNS)

    async def resolve(filter_tree: Filter) -> np.ndarray:
        sql_clause, params = adapter.adapt(filter_tree)
        sql = f"SELECT id FROM chunks WHERE {sql_clause}"
        async with _maybe_acquire(provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())
        # ``np.asarray([], dtype=np.uint64)`` preserves the dtype on the
        # empty-result path; numpy would otherwise infer float64 from [].
        return np.asarray([r[0] for r in rows], dtype=np.uint64)

    return resolve


async def check_integrity_and_repair(
    *,
    db_path: Path,
    tq_path: Path,
    dim: int,
    bit_width: int,
) -> list[str]:
    """Compare ``chunks`` row count vs TurboQuant ``size()``; repair drift.

    Composite SQLite + TurboQuant deployments are not strictly cross-backend
    ACID (see :class:`CompositeUnitOfWork` docstring). A crash between the
    SQLite commit and the TurboQuant ``.tq`` write can leave the two
    backends out of sync. This startup hook detects the drift by
    counting both sides; on mismatch it logs a warning and clears
    ``packages.content_hash`` on every package so the next indexing sweep
    treats them as stale and re-extracts (re-embedding the chunks in the
    process). Returns the list of repaired package names so callers can
    surface them in logs / metrics.

    The fresh-project case is intentional: when neither backend has any
    rows yet (``chunk_count == 0 == vec_count``) the function is a no-op
    and returns ``[]``. ``TurboQuantUnitOfWork.__aenter__`` synthesises an
    empty in-memory index for a missing ``.tq`` file, so ``size() == 0``
    matches an empty ``chunks`` table — no false alarm. Per spec §5.7
    (cache is regenerable; silent recovery preserves user flow).

    Assumes the configured ingestion pipeline writes one embedding per chunk
    (the shipped default does — ``embed_chunks`` stage with strict=True 1:1
    zip). If a custom ingestion.yaml omits embed_chunks, chunks > vectors
    becomes the expected steady state and this helper will trigger
    persistent false positives. Skip via custom startup wiring in that case.
    """

    def _chunk_count() -> int:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        finally:
            conn.close()

    chunk_count = await asyncio.to_thread(_chunk_count)
    async with TurboQuantUnitOfWork(
        index_path=tq_path,
        dim=dim,
        bit_width=bit_width,
    ) as tq_uow:
        vec_count = tq_uow.size()
    if chunk_count == vec_count:
        return []

    logger.warning(
        "Cache integrity mismatch: chunks=%d but TurboQuant index "
        "size=%d. Clearing content_hash on affected packages so the "
        "next indexing sweep re-extracts them.",
        chunk_count,
        vec_count,
    )

    def _clear_all_hashes() -> list[str]:
        conn = sqlite3.connect(str(db_path))
        try:
            names = [r[0] for r in conn.execute("SELECT name FROM packages")]
            conn.execute("UPDATE packages SET content_hash = NULL")
            conn.commit()
            return names
        finally:
            conn.close()

    return await asyncio.to_thread(_clear_all_hashes)


def build_sqlite_chunk_hydrator(
    db_path: Path,
) -> Callable[[Sequence[int]], Awaitable[tuple[Chunk, ...]]]:
    """Build a ChunkHydrator — loads full ``Chunk`` objects for the given IDs.

    Used by ``TurboQuantVectorStore`` to turn vector hits (just IDs) back into
    rich ``Chunk`` records the retrieval pipeline can consume. Reuses
    ``row_to_chunk`` so the deserialisation contract matches the rest of
    the SQLite adapter — any schema drift surfaces uniformly.
    """
    provider = build_connection_provider(db_path)

    async def hydrate(ids: Sequence[int]) -> tuple[Chunk, ...]:
        if not ids:
            return ()
        id_list = list(ids)
        placeholders = ",".join("?" * len(id_list))
        # ``SELECT *`` keeps the column list in lockstep with the schema —
        # ``row_to_chunk`` reads named columns from the ``sqlite3.Row`` so
        # additive migrations (e.g., the v5 ``content_hash`` column) don't
        # require touching this query.
        sql = f"SELECT * FROM chunks WHERE id IN ({placeholders})"
        async with _maybe_acquire(provider) as conn:
            # Performance: ``row_to_chunk`` is pure CPU work — bundling
            # the fetch + map into a single ``to_thread`` call keeps the
            # whole hydration off the event-loop thread, matching the
            # ``SqliteChunkRepository.list`` pattern.
            return await asyncio.to_thread(
                lambda: tuple(row_to_chunk(r) for r in conn.execute(sql, id_list).fetchall())
            )

    return hydrate
