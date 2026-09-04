"""SqliteBranchChunkRepository — BranchChunkStore over ``branch_chunks`` (spec §6.1)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.models import BranchSlice
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.branch_records import ChunkMembership
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire

# Injection boundary: the column list is a literal here; every value binds
# as a named parameter, never interpolated.
_INSERT_SQL = (
    "INSERT INTO branch_chunks (branch, chunk_id, source_path, start_line, end_line, changed, slice) "
    "VALUES (:branch, :chunk_id, :source_path, :start_line, :end_line, :changed, :slice)"
)
_SELECT_SQL = (
    "SELECT branch, chunk_id, source_path, start_line, end_line, changed, slice "
    "FROM branch_chunks WHERE branch = ? ORDER BY source_path, start_line, chunk_id"
)


def _membership_to_row(m: ChunkMembership) -> dict[str, object]:
    return {
        "branch": m.branch,
        "chunk_id": m.chunk_id,
        "source_path": m.source_path,
        "start_line": m.start_line,
        "end_line": m.end_line,
        "changed": int(m.changed),
        "slice": m.slice.value,
    }


def _row_to_membership(row: sqlite3.Row) -> ChunkMembership:
    return ChunkMembership(
        branch=row["branch"],
        chunk_id=row["chunk_id"],
        source_path=row["source_path"],
        start_line=row["start_line"],
        end_line=row["end_line"],
        changed=bool(row["changed"]),
        slice=BranchSlice(row["slice"]),
    )


@dataclass(frozen=True, slots=True)
class SqliteBranchChunkRepository:
    """BranchChunkStore backed by the ``branch_chunks`` table (spec §6.1)."""

    provider: ConnectionProvider

    async def replace_membership(self, branch: str, rows: Sequence[ChunkMembership]) -> None:
        """Atomic swap: the branch's membership becomes exactly ``rows``."""
        params = [_membership_to_row(m) for m in rows]
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute, "DELETE FROM branch_chunks WHERE branch = ?", (branch,)
            )
            await asyncio.to_thread(conn.executemany, _INSERT_SQL, params)

    async def list_membership(self, branch: str) -> tuple[ChunkMembership, ...]:
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(_SELECT_SQL, (branch,)).fetchall())
        return tuple(_row_to_membership(r) for r in rows)

    async def count_for_branch(self, branch: str) -> int:
        sql = "SELECT COUNT(*) FROM branch_chunks WHERE branch = ?"
        async with _maybe_acquire(self.provider) as conn:
            return int(await asyncio.to_thread(lambda: conn.execute(sql, (branch,)).fetchone()[0]))

    async def delete_for_branch(self, branch: str) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute, "DELETE FROM branch_chunks WHERE branch = ?", (branch,)
            )

    async def delete_all(self) -> None:
        """Unconditional sweep (spec I3) — :meth:`SqliteUnitOfWork.delete_all` driver."""
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, "DELETE FROM branch_chunks")
