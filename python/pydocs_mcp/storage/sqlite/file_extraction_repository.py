"""SqliteFileExtractionRepository — FileExtractionStore over ``file_extractions`` (spec §6.1)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.retrieval.pipeline.connection import sqlite_to_thread
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.branch_records import FileExtraction
from pydocs_mcp.storage.sqlite.table_crud import ID_BATCH_SIZE, delete_all_rows
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire

# Injection boundary: the table name the CRUD helpers interpolate comes only
# from this constant — never caller input.
_TABLE = "file_extractions"

_COLUMNS = (
    "blob_sha",
    "path",
    "pipeline_hash",
    "chunk_spans",
    "tree_json",
    "members_json",
    "references_json",
    "created_at",
)
# COALESCE keeps a P1-populated tree/members/references column when a later
# spans-only write (P0 shape) lands on the same key.
# Injection boundary: column names come from the module constant above,
# never caller input; values always bind as named parameters.
_UPSERT_SQL = (
    f"INSERT INTO file_extractions ({', '.join(_COLUMNS)}) VALUES "
    f"({', '.join(':' + c for c in _COLUMNS)}) "
    "ON CONFLICT(blob_sha, path, pipeline_hash) DO UPDATE SET "
    "chunk_spans=excluded.chunk_spans, "
    "tree_json=COALESCE(excluded.tree_json, tree_json), "
    "members_json=COALESCE(excluded.members_json, members_json), "
    "references_json=COALESCE(excluded.references_json, references_json), "
    "created_at=excluded.created_at"
)
_GET_SQL = (
    f"SELECT {', '.join(_COLUMNS)} FROM file_extractions "
    "WHERE blob_sha = ? AND path = ? AND pipeline_hash = ?"
)
_DELETE_UNREFERENCED_SQL = (
    "DELETE FROM file_extractions WHERE NOT EXISTS (SELECT 1 FROM branch_files bf "
    "WHERE bf.blob_sha = file_extractions.blob_sha AND bf.path = file_extractions.path)"
)
# The ``pipeline_hash`` column holds the extraction key (#261, #309).
_DELETE_SUPERSEDED_SQL = "DELETE FROM file_extractions WHERE pipeline_hash <> ?"
# The spans are parsed in Python, not with SQLite's JSON1 functions, which the
# supported SQLite builds are not all guaranteed to carry.
_SELECT_SPANS_SQL = "SELECT rowid, chunk_spans FROM file_extractions"


def _extraction_to_row(r: FileExtraction) -> dict[str, object]:
    return {
        "blob_sha": r.blob_sha,
        "path": r.path,
        "pipeline_hash": r.pipeline_hash,
        "chunk_spans": r.chunk_spans,
        "tree_json": r.tree_json,
        "members_json": r.members_json,
        "references_json": r.references_json,
        "created_at": r.created_at,
    }


def _row_to_extraction(row: sqlite3.Row) -> FileExtraction:
    return FileExtraction(
        blob_sha=row["blob_sha"],
        path=row["path"],
        pipeline_hash=row["pipeline_hash"],
        chunk_spans=row["chunk_spans"],
        created_at=row["created_at"],
        tree_json=row["tree_json"],
        members_json=row["members_json"],
        references_json=row["references_json"],
    )


@dataclass(frozen=True, slots=True)
class SqliteFileExtractionRepository:
    """FileExtractionStore backed by the ``file_extractions`` table (spec §6.1)."""

    provider: ConnectionProvider

    async def upsert_many(self, rows: Sequence[FileExtraction]) -> None:
        params = [_extraction_to_row(r) for r in rows]
        if not params:
            return
        async with _maybe_acquire(self.provider) as conn:
            await sqlite_to_thread(conn.executemany, _UPSERT_SQL, params)

    async def get(self, blob_sha: str, path: str, pipeline_hash: str) -> FileExtraction | None:
        async with _maybe_acquire(self.provider) as conn:
            row = await sqlite_to_thread(
                lambda: conn.execute(_GET_SQL, (blob_sha, path, pipeline_hash)).fetchone()
            )
        return _row_to_extraction(row) if row else None

    async def delete_unreferenced(self) -> int:
        """Drop rows whose ``(blob_sha, path)`` no ``branch_files`` row references."""
        async with _maybe_acquire(self.provider) as conn:
            cursor = await sqlite_to_thread(conn.execute, _DELETE_UNREFERENCED_SQL)
        return int(cursor.rowcount)

    async def delete_superseded(self, extraction_cache_key: str) -> int:
        """Drop rows keyed by any other extraction key (#261, #309)."""
        async with _maybe_acquire(self.provider) as conn:
            cursor = await sqlite_to_thread(
                conn.execute, _DELETE_SUPERSEDED_SQL, (extraction_cache_key,)
            )
        return int(cursor.rowcount)

    async def delete_naming_chunk_ids(self, ids: Sequence[int]) -> int:
        """Drop rows whose ``chunk_spans`` name any of ``ids`` (#310)."""
        if not ids:
            return 0
        freed = frozenset(ids)
        async with _maybe_acquire(self.provider) as conn:
            return await sqlite_to_thread(_delete_rows_naming, conn, freed)

    async def delete_all(self) -> None:
        """Unconditional sweep (spec I3) — :meth:`SqliteUnitOfWork.delete_all` driver."""
        await delete_all_rows(self.provider, table=_TABLE)


def _spans_name_any(chunk_spans: str, ids: frozenset[int]) -> bool:
    """True when one ``[chunk_id, start, end]`` span of the JSON names an id in ``ids``."""
    return any(int(span[0]) in ids for span in json.loads(chunk_spans))


def _delete_rows_naming(conn: sqlite3.Connection, ids: frozenset[int]) -> int:
    stale = [row[0] for row in conn.execute(_SELECT_SPANS_SQL) if _spans_name_any(row[1], ids)]
    for start in range(0, len(stale), ID_BATCH_SIZE):
        batch = stale[start : start + ID_BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        conn.execute(f"DELETE FROM file_extractions WHERE rowid IN ({placeholders})", batch)
    return len(stale)
