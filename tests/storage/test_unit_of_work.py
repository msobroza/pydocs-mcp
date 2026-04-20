"""Tests for SqliteUnitOfWork + _maybe_acquire (spec §5.3)."""
from __future__ import annotations

import sqlite3

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.storage.sqlite import (
    SqliteUnitOfWork,
    _maybe_acquire,
    _sqlite_transaction,
)


@pytest.fixture
def db_file(tmp_path):
    f = tmp_path / "uow.db"
    open_index_database(f).close()
    return f


async def test_maybe_acquire_without_ambient_opens_fresh(db_file):
    provider = build_connection_provider(db_file)
    async with _maybe_acquire(provider) as conn:
        assert isinstance(conn, sqlite3.Connection)


async def test_maybe_acquire_reuses_ambient(db_file):
    import asyncio as _asyncio

    provider = build_connection_provider(db_file)
    # Pretend a UoW has installed an ambient (conn, lock) pair
    real = sqlite3.connect(str(db_file))
    lock = _asyncio.Lock()
    token = _sqlite_transaction.set((real, lock))
    try:
        async with _maybe_acquire(provider) as conn:
            assert conn is real
    finally:
        _sqlite_transaction.reset(token)
        real.close()


async def test_unit_of_work_commits_on_success(db_file):
    provider = build_connection_provider(db_file)
    uow = SqliteUnitOfWork(provider=provider)

    async with uow.begin():
        async with _maybe_acquire(provider) as conn:
            conn.execute(
                "INSERT INTO packages (name, version, summary, homepage, "
                "dependencies, content_hash, origin) VALUES (?,?,?,?,?,?,?)",
                ("test_pkg", "1.0", "", "", "[]", "h", "dependency"),
            )

    # After commit, the row must be visible on a fresh connection
    fresh = sqlite3.connect(str(db_file))
    count = fresh.execute("SELECT COUNT(*) FROM packages WHERE name=?", ("test_pkg",)).fetchone()[0]
    fresh.close()
    assert count == 1


async def test_unit_of_work_rollbacks_on_exception(db_file):
    provider = build_connection_provider(db_file)
    uow = SqliteUnitOfWork(provider=provider)

    with pytest.raises(RuntimeError, match="boom"):
        async with uow.begin():
            async with _maybe_acquire(provider) as conn:
                conn.execute(
                    "INSERT INTO packages (name, version, summary, homepage, "
                    "dependencies, content_hash, origin) VALUES (?,?,?,?,?,?,?)",
                    ("rolled_back", "1.0", "", "", "[]", "h", "dependency"),
                )
            raise RuntimeError("boom")

    fresh = sqlite3.connect(str(db_file))
    count = fresh.execute("SELECT COUNT(*) FROM packages WHERE name=?", ("rolled_back",)).fetchone()[0]
    fresh.close()
    assert count == 0


async def test_unit_of_work_serializes_concurrent_repo_calls(db_file):
    """Two ``asyncio.gather``-ed repo calls inside one UoW must not race the
    shared sqlite3.Connection. The asyncio.Lock on SqliteUnitOfWork serialises
    them — both succeed and neither raises.

    sqlite3.Connection is not thread-safe for concurrent statement execution;
    two ``to_thread`` calls sharing the same conn would otherwise produce UB
    (interleaved SQL, corrupted transaction, or explicit errors depending on
    the CPython build flags).
    """
    import asyncio as _asyncio

    from pydocs_mcp.models import Chunk, ChunkFilterField
    from pydocs_mcp.storage.sqlite import SqliteChunkRepository

    provider = build_connection_provider(db_file)
    uow = SqliteUnitOfWork(provider=provider)
    repo = SqliteChunkRepository(provider=provider)

    def _chunk(title: str) -> Chunk:
        return Chunk(
            text="body " + title,
            metadata={
                ChunkFilterField.PACKAGE.value: "p",
                ChunkFilterField.TITLE.value: title,
                ChunkFilterField.ORIGIN.value: "readme",
            },
        )

    async with uow.begin():
        await _asyncio.gather(
            repo.upsert([_chunk("a"), _chunk("b")]),
            repo.upsert([_chunk("c"), _chunk("d")]),
        )

    fresh = sqlite3.connect(str(db_file))
    titles = {
        r[0] for r in fresh.execute("SELECT title FROM chunks").fetchall()
    }
    fresh.close()
    assert titles == {"a", "b", "c", "d"}
