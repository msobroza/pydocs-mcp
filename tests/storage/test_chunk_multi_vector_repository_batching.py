"""Regression: SqliteChunkMultiVectorRepository must batch large id lists.

``delete_by_chunk_ids`` / ``packages_for_chunks`` / ``plaid_ids_for_chunks``
build a single unbatched ``IN (?,?,...)`` clause sized to the caller's
entire id list. ``chunk_repository.py``'s ``delete_by_ids`` / ``mark_embedded``
document 500-row batching as mandatory to stay under
``SQLITE_MAX_VARIABLE_NUMBER`` (999 on older SQLite builds); the multi-vector
repo has no such batching, so a single large package reindex with
late-interaction enabled can raise ``sqlite3.OperationalError: too many SQL
variables`` and roll back the whole composite transaction.

The default ``.venv`` SQLite is compiled with a high
``SQLITE_MAX_VARIABLE_NUMBER`` (see ``PRAGMA compile_options``), so simply
calling these methods with ~1200 ids would NOT reproduce the failure on
this machine. To pin the exact edge case portably, ``_LowLimitProvider``
wraps the real connection provider and calls
``sqlite3.Connection.setlimit(SQLITE_LIMIT_VARIABLE_NUMBER, ...)`` right
after opening each connection — reproducing a 999-variable-limit SQLite
build deterministically on any machine/CI runner.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.storage.factories import build_connection_provider
from pydocs_mcp.storage.sqlite import SqliteChunkMultiVectorRepository, SqliteUnitOfWork

# Mirrors the older-SQLite-build ceiling chunk_repository.py's batching
# comment cites (SQLITE_MAX_VARIABLE_NUMBER default of 999 pre-3.32).
_LOW_VARIABLE_LIMIT = 999
# Comfortably over the limit so a single unbatched IN-clause call is
# guaranteed to overflow it (the gap's suggested ~1200 scale).
_ID_COUNT = 1200


@dataclass(frozen=True, slots=True)
class _LowLimitProvider:
    """Wraps a real ``ConnectionProvider``, capping SQLITE_LIMIT_VARIABLE_NUMBER.

    Each acquired connection gets its variable-number limit clamped down to
    ``_LOW_VARIABLE_LIMIT`` right after opening, so an unbatched ``IN (...)``
    clause built from more than that many ids raises the SAME
    ``sqlite3.OperationalError: too many SQL variables`` a legacy-SQLite
    production deployment would hit at 999 ids — without depending on
    however this machine's SQLite happens to be compiled.
    """

    cache_path: Path

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, _LOW_VARIABLE_LIMIT)
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def acquire_sync(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, _LOW_VARIABLE_LIMIT)
        try:
            yield conn
        finally:
            conn.close()


async def _seed_many_chunks(db_path: Path, count: int) -> list[int]:
    """Insert ``count`` chunks via the real provider; return their ids in order."""
    import asyncio

    provider = build_connection_provider(db_path)

    def _insert() -> list[int]:
        with provider.acquire_sync() as conn:
            conn.executemany(
                "INSERT INTO chunks(package, title, text, origin) VALUES(?,?,?,'dep_doc')",
                [(f"pkg{i}", f"t{i}", "b") for i in range(count)],
            )
            conn.commit()
            rows = conn.execute("SELECT id FROM chunks ORDER BY id").fetchall()
        return [row[0] for row in rows]

    return await asyncio.to_thread(_insert)


@pytest.mark.asyncio
async def test_delete_by_chunk_ids_batches_past_variable_limit(tmp_path: Path) -> None:
    """delete_by_chunk_ids must not overflow SQLITE_LIMIT_VARIABLE_NUMBER."""
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    ids = await _seed_many_chunks(db_path, _ID_COUNT)
    assert len(ids) == _ID_COUNT

    real_provider = build_connection_provider(db_path)
    uow = SqliteUnitOfWork(provider=real_provider)
    async with uow:
        repo = SqliteChunkMultiVectorRepository(provider=real_provider)
        await repo.upsert([(chunk_id, offset, "p", "h") for offset, chunk_id in enumerate(ids)])
        await uow.commit()

    low_limit_provider = _LowLimitProvider(cache_path=db_path)
    repo = SqliteChunkMultiVectorRepository(provider=low_limit_provider)
    deleted = await repo.delete_by_chunk_ids(ids)
    assert set(deleted) == set(range(_ID_COUNT))

    # Confirm the rows are actually gone (not a silent partial failure).
    real_repo = SqliteChunkMultiVectorRepository(provider=real_provider)
    assert await real_repo.plaid_ids_for_chunks(ids) == ()


@pytest.mark.asyncio
async def test_packages_for_chunks_batches_past_variable_limit(tmp_path: Path) -> None:
    """packages_for_chunks must not overflow SQLITE_LIMIT_VARIABLE_NUMBER."""
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    ids = await _seed_many_chunks(db_path, _ID_COUNT)

    low_limit_provider = _LowLimitProvider(cache_path=db_path)
    repo = SqliteChunkMultiVectorRepository(provider=low_limit_provider)
    result = await repo.packages_for_chunks(ids)

    assert result == {chunk_id: f"pkg{i}" for i, chunk_id in enumerate(ids)}


@pytest.mark.asyncio
async def test_plaid_ids_for_chunks_batches_past_variable_limit(tmp_path: Path) -> None:
    """plaid_ids_for_chunks must not overflow SQLITE_LIMIT_VARIABLE_NUMBER."""
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    ids = await _seed_many_chunks(db_path, _ID_COUNT)

    real_provider = build_connection_provider(db_path)
    uow = SqliteUnitOfWork(provider=real_provider)
    async with uow:
        repo = SqliteChunkMultiVectorRepository(provider=real_provider)
        await repo.upsert([(chunk_id, offset, "p", "h") for offset, chunk_id in enumerate(ids)])
        await uow.commit()

    low_limit_provider = _LowLimitProvider(cache_path=db_path)
    repo = SqliteChunkMultiVectorRepository(provider=low_limit_provider)
    result = await repo.plaid_ids_for_chunks(ids)

    assert dict(result) == {offset: chunk_id for offset, chunk_id in enumerate(ids)}
