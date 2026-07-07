"""SqliteChunkRepository — ChunkStore over the ``chunks`` table (spec §5.3, AC #9)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from pydocs_mcp.filters import Filter
from pydocs_mcp.models import Chunk
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.sqlite.filter_adapter import (
    CHUNK_COLUMNS,
    _SqliteFilterTranslator,
)
from pydocs_mcp.storage.sqlite.row_mappers import _chunk_to_row, row_to_chunk
from pydocs_mcp.storage.sqlite.table_crud import (
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
    "(package, module, title, text, origin, content_hash, qualified_name, decision_id) "
    "VALUES "
    "(:package, :module, :title, :text, :origin, :content_hash, :qualified_name, :decision_id)"
)


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
        # Performance: batch at 500 to stay safely under SQLITE_MAX_VARIABLE_NUMBER
        # (default 999 in older SQLite builds; 32766 in newer ones — 500 is
        # well under both and limits per-statement parsing cost).
        async with _maybe_acquire(self.provider) as conn:
            for i in range(0, len(ids), 500):
                batch = ids[i : i + 500]
                placeholders = ",".join("?" * len(batch))
                await asyncio.to_thread(
                    conn.execute,
                    f"DELETE FROM chunks WHERE id IN ({placeholders})",
                    list(batch),
                )

    async def mark_embedded(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        # Same 500-row batching rationale as delete_by_ids above.
        async with _maybe_acquire(self.provider) as conn:
            for i in range(0, len(ids), 500):
                batch = ids[i : i + 500]
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

    async def delete_all(self) -> None:
        """Unconditional sweep (spec I3) — :class:`SqliteUnitOfWork.delete_all` driver."""
        await delete_all_rows(self.provider, table=_TABLE)
