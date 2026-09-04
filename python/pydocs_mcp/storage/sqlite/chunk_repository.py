"""SqliteChunkRepository — ChunkStore over the ``chunks`` table (spec §5.3, AC #9)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from pydocs_mcp.filters import Filter
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.sqlite.filter_adapter import (
    CHUNK_COLUMNS,
    _SqliteFilterTranslator,
)
from pydocs_mcp.storage.sqlite.row_mappers import _chunk_to_row, row_to_chunk
from pydocs_mcp.storage.sqlite.table_crud import (
    ID_BATCH_SIZE,
    _resolve_filter,
    count_rows,
    delete_all_rows,
    delete_rows,
    list_rows,
)
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire

# Injection boundary: the table name the CRUD helpers interpolate comes
# only from this constant — never caller input.
_TABLE = "chunks"

# Single source of truth for the chunk write column list (spec §5.3): ``upsert``
# and ``insert`` share it verbatim (SQLite INSERT with no conflict clause IS the
# insert-only semantic), so the column set can't drift between the two paths.
# ``decision_id`` (schema v14) is a nullable backlink — None for non-decision
# chunks; :func:`_chunk_to_row` supplies it. All values are named params bound
# from the row dict, never interpolated, so there's no injection surface.
_INSERT_CHUNK_SQL = (
    "INSERT INTO chunks "
    "(package, module, title, text, origin, content_hash, qualified_name, decision_id, "
    "source_path, start_line, end_line) "
    "VALUES "
    "(:package, :module, :title, :text, :origin, :content_hash, :qualified_name, :decision_id, "
    ":source_path, :start_line, :end_line)"
)

# v15 span backfill (ChunkStore.refresh_span_metadata): the SET list touches
# ONLY the three span columns — id / embedded / decision_id survive, and no
# FTS content changes (chunks_fts indexes title/text/package, none of which
# appear here). Spans are outside content_hash, so this never re-triggers a
# re-embed either. All values are named params, never interpolated.
_REFRESH_SPAN_SQL = (
    "UPDATE chunks SET source_path = :source_path, "
    "start_line = :start_line, end_line = :end_line "
    "WHERE package = :package AND module = :module AND content_hash = :content_hash"
)


_UNREFERENCED_PROJECT_SQL = (
    "SELECT id FROM chunks WHERE package = ? AND NOT EXISTS "
    "(SELECT 1 FROM branch_chunks bc WHERE bc.chunk_id = chunks.id)"
)


def _insert_rows_returning_ids(
    conn: sqlite3.Connection,
    rows: list[dict[str, object]],
) -> tuple[int, ...]:
    # Per-row execute so ``lastrowid`` is exact on every supported SQLite
    # (``INSERT … RETURNING`` needs 3.35+, which the manylinux floor does not
    # promise). One statement per chunk inside one transaction is cheap.
    ids: list[int] = []
    for row in rows:
        cursor = conn.execute(_INSERT_CHUNK_SQL, row)
        ids.append(int(cursor.lastrowid))
    return tuple(ids)


def _span_refresh_params(package: str, chunks: Sequence[Chunk]) -> list[dict[str, object]]:
    """Named-param rows for ``_REFRESH_SPAN_SQL`` — one per kept chunk.

    Reuses ``_chunk_to_row`` so span normalization (empty-string
    ``source_path`` → NULL) can't drift from the insert path. ``package``
    comes from the caller (the reindex target), not chunk metadata.
    """
    params: list[dict[str, object]] = []
    for chunk in chunks:
        row = _chunk_to_row(chunk)
        params.append(
            {
                "source_path": row["source_path"],
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "package": package,
                "module": row["module"],
                "content_hash": row["content_hash"],
            }
        )
    return params


@dataclass(frozen=True, slots=True)
class SqliteChunkRepository:
    """ChunkStore backed by the 'chunks' SQLite table (spec §5.3, AC #9).

    CRUD only — text retrieval lives in ``SqliteLexicalStore``. ``rebuild_index``
    refreshes the ``chunks_fts`` content-backed virtual table after bulk writes.
    """

    provider: ConnectionProvider
    filter_adapter: _SqliteFilterTranslator = field(
        default_factory=lambda: _SqliteFilterTranslator(safe_columns=CHUNK_COLUMNS)
    )

    async def upsert(self, chunks: Iterable[Chunk]) -> None:
        rows = [_chunk_to_row(c) for c in chunks]
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.executemany, _INSERT_CHUNK_SQL, rows)

    async def list(
        self,
        filter: Filter | Mapping | None = None,
        limit: int | None = None,
    ) -> list[Chunk]:
        return await list_rows(
            self.provider,
            self.filter_adapter,
            table=_TABLE,
            mapper=row_to_chunk,
            filter=filter,
            limit=limit,
        )

    async def delete(self, filter: Filter | Mapping) -> int:
        return await delete_rows(self.provider, self.filter_adapter, table=_TABLE, filter=filter)

    async def count(self, filter: Filter | Mapping | None = None) -> int:
        return await count_rows(self.provider, self.filter_adapter, table=_TABLE, filter=filter)

    async def rebuild_index(self) -> None:
        """Rebuild the chunks_fts virtual table so newly-inserted rows are searchable."""
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')",
            )

    async def list_id_hash_pairs(
        self,
        *,
        filter: Filter | Mapping | None = None,
    ) -> tuple[tuple[int, str | None], ...]:
        tree = _resolve_filter(filter)
        where, params = "", []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
        sql = "SELECT id, content_hash FROM chunks"
        if where:
            sql += f" WHERE {where}"
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())
        return tuple((row["id"], row["content_hash"]) for row in rows)

    async def delete_by_ids(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        async with _maybe_acquire(self.provider) as conn:
            for i in range(0, len(ids), ID_BATCH_SIZE):
                batch = ids[i : i + ID_BATCH_SIZE]
                placeholders = ",".join("?" * len(batch))
                await asyncio.to_thread(
                    conn.execute,
                    f"DELETE FROM chunks WHERE id IN ({placeholders})",
                    list(batch),
                )

    async def mark_embedded(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        async with _maybe_acquire(self.provider) as conn:
            for i in range(0, len(ids), ID_BATCH_SIZE):
                batch = ids[i : i + ID_BATCH_SIZE]
                placeholders = ",".join("?" * len(batch))
                await asyncio.to_thread(
                    conn.execute,
                    f"UPDATE chunks SET embedded = 1 WHERE id IN ({placeholders})",
                    list(batch),
                )

    async def insert(self, chunks: tuple[Chunk, ...]) -> None:
        # SQL is identical to upsert (SQLite INSERT with no conflict clause
        # IS the insert-only semantic). The two methods are kept distinct
        # to make caller intent explicit — the diff-merge wants insert-only,
        # while the legacy "wipe and rewrite" path uses upsert.
        rows = [_chunk_to_row(c) for c in chunks]
        if not rows:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.executemany, _INSERT_CHUNK_SQL, rows)

    async def insert_returning_ids(self, chunks: tuple[Chunk, ...]) -> tuple[int, ...]:
        """Insert-only, reporting the new row ids in input order.

        See :class:`~pydocs_mcp.storage.protocols.ChunkStore` for the contract.
        """
        rows = [_chunk_to_row(c) for c in chunks]
        if not rows:
            return ()
        async with _maybe_acquire(self.provider) as conn:
            return await asyncio.to_thread(_insert_rows_returning_ids, conn, rows)

    async def delete_unreferenced_project_chunks(self) -> tuple[int, ...]:
        """Project-scoped GC — see the ``ChunkStore`` Protocol for the contract."""
        # Two acquisitions on purpose: ``delete_by_ids`` re-enters
        # ``_maybe_acquire``, and the ambient lock is not re-entrant.
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(_UNREFERENCED_PROJECT_SQL, (PROJECT_PACKAGE_NAME,)).fetchall()
            )
        ids = [row["id"] for row in rows]
        await self.delete_by_ids(ids)
        return tuple(ids)

    async def refresh_span_metadata(self, package: str, chunks: Sequence[Chunk]) -> None:
        """Refresh the v15 span columns on hash-matched kept rows.

        See :class:`~pydocs_mcp.storage.protocols.ChunkStore` for the
        contract; ``_REFRESH_SPAN_SQL`` above documents the invariants
        (span columns only, no FTS impact, no re-embed).
        """
        params = _span_refresh_params(package, chunks)
        if not params:
            return
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.executemany, _REFRESH_SPAN_SQL, params)

    async def delete_all(self) -> None:
        """Unconditional sweep (spec I3) — :class:`SqliteUnitOfWork.delete_all` driver."""
        await delete_all_rows(self.provider, table=_TABLE)
