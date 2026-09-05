"""SqliteBranchRepository — BranchStore over ``branches`` + ``branch_files`` (spec §6.1)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.models import BranchIndexSource, BranchStatus, FileChangeKind
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.branch_records import BranchFile, BranchRecord
from pydocs_mcp.storage.sqlite.table_crud import delete_all_rows
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire

# Injection boundary: the table names the CRUD helpers interpolate come only
# from these constants — never caller input.
_TABLE = "branches"
_FILES_TABLE = "branch_files"

_BRANCH_COLUMNS = (
    "name",
    "head_sha",
    "base_name",
    "merge_base_sha",
    "source",
    "worktree_path",
    "is_default",
    "pipeline_hash",
    "indexed_at",
    "last_used_at",
    "status",
    "merged_into",
    "retired_at",
    "purge_after",
    "pinned",
)
# Injection boundary: every column name below comes from the module constant
# above, never caller input; values always bind as named parameters.
_UPSERT_BRANCH_SQL = (
    f"INSERT INTO branches ({', '.join(_BRANCH_COLUMNS)}) VALUES "
    f"({', '.join(':' + c for c in _BRANCH_COLUMNS)}) ON CONFLICT(name) DO UPDATE SET "
    + ", ".join(f"{c}=excluded.{c}" for c in _BRANCH_COLUMNS if c != "name")
)
_SELECT_BRANCH_SQL = f"SELECT {', '.join(_BRANCH_COLUMNS)} FROM branches"
_INSERT_FILE_SQL = (
    "INSERT INTO branch_files (branch, path, blob_sha, change_kind) "
    "VALUES (:branch, :path, :blob_sha, :change_kind)"
)
_SELECT_FILES_SQL = (
    "SELECT branch, path, blob_sha, change_kind FROM branch_files WHERE branch = ? ORDER BY path"
)


def _branch_to_row(r: BranchRecord) -> dict[str, object]:
    return {
        "name": r.name,
        "head_sha": r.head_sha,
        "base_name": r.base_name,
        "merge_base_sha": r.merge_base_sha,
        "source": r.source.value,
        "worktree_path": r.worktree_path,
        "is_default": int(r.is_default),
        "pipeline_hash": r.pipeline_hash,
        "indexed_at": r.indexed_at,
        "last_used_at": r.last_used_at,
        "status": r.status.value,
        "merged_into": r.merged_into,
        "retired_at": r.retired_at,
        "purge_after": r.purge_after,
        "pinned": int(r.pinned),
    }


def _row_to_branch(row: sqlite3.Row) -> BranchRecord:
    return BranchRecord(
        name=row["name"],
        head_sha=row["head_sha"],
        base_name=row["base_name"],
        merge_base_sha=row["merge_base_sha"],
        source=BranchIndexSource(row["source"]),
        worktree_path=row["worktree_path"],
        is_default=bool(row["is_default"]),
        pipeline_hash=row["pipeline_hash"],
        indexed_at=row["indexed_at"],
        last_used_at=row["last_used_at"],
        status=BranchStatus(row["status"]),
        merged_into=row["merged_into"],
        retired_at=row["retired_at"],
        purge_after=row["purge_after"],
        pinned=bool(row["pinned"]),
    )


def _row_to_file(row: sqlite3.Row) -> BranchFile:
    return BranchFile(
        branch=row["branch"],
        path=row["path"],
        blob_sha=row["blob_sha"],
        change_kind=FileChangeKind(row["change_kind"]),
    )


@dataclass(frozen=True, slots=True)
class SqliteBranchRepository:
    """BranchStore backed by the ``branches`` + ``branch_files`` tables (spec §6.1)."""

    provider: ConnectionProvider

    async def upsert_branch(self, record: BranchRecord) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.execute, _UPSERT_BRANCH_SQL, _branch_to_row(record))

    async def get_branch(self, name: str) -> BranchRecord | None:
        sql = _SELECT_BRANCH_SQL + " WHERE name = ?"
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(lambda: conn.execute(sql, (name,)).fetchone())
        return _row_to_branch(row) if row else None

    async def list_branches(self) -> tuple[BranchRecord, ...]:
        sql = _SELECT_BRANCH_SQL + " ORDER BY is_default DESC, name"
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql).fetchall())
        return tuple(_row_to_branch(r) for r in rows)

    async def default_branch_name(self) -> str | None:
        sql = "SELECT name FROM branches WHERE is_default = 1 ORDER BY indexed_at DESC LIMIT 1"
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(lambda: conn.execute(sql).fetchone())
        return str(row["name"]) if row else None

    async def replace_files(self, branch: str, files: Sequence[BranchFile]) -> None:
        """Atomic swap: the branch's manifest becomes exactly ``files``.

        ``branch`` comes from the caller, not from each row, so a manifest
        assembled for one branch can never leak rows into another.
        """
        rows = [
            {
                "branch": branch,
                "path": f.path,
                "blob_sha": f.blob_sha,
                "change_kind": f.change_kind.value,
            }
            for f in files
        ]
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute, "DELETE FROM branch_files WHERE branch = ?", (branch,)
            )
            await asyncio.to_thread(conn.executemany, _INSERT_FILE_SQL, rows)

    async def list_files(self, branch: str) -> tuple[BranchFile, ...]:
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(_SELECT_FILES_SQL, (branch,)).fetchall()
            )
        return tuple(_row_to_file(r) for r in rows)

    async def count_files(self, branch: str) -> int:
        sql = "SELECT COUNT(*) FROM branch_files WHERE branch = ?"
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(lambda: conn.execute(sql, (branch,)).fetchone())
        return int(row[0])

    async def delete_branch(self, name: str) -> None:
        """Drop the record AND its manifest rows — children first."""
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute, "DELETE FROM branch_files WHERE branch = ?", (name,)
            )
            await asyncio.to_thread(conn.execute, "DELETE FROM branches WHERE name = ?", (name,))

    async def delete_all(self) -> None:
        """Unconditional sweep (spec I3) — :meth:`SqliteUnitOfWork.delete_all` driver.

        Children first, same order as :meth:`delete_branch`.
        """
        await delete_all_rows(self.provider, table=_FILES_TABLE)
        await delete_all_rows(self.provider, table=_TABLE)
