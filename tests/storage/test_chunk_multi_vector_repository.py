"""Unit tests for SqliteChunkMultiVectorRepository.

Exercises upsert / next_plaid_offset / delete_by_chunk_ids / clear /
packages_for_chunks / plaid_ids_for_chunks against a real temp SQLite DB
built via ``open_index_database``. SQLite-only — runs in the default
``.venv`` without fast-plaid or torch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.storage.factories import build_connection_provider
from pydocs_mcp.storage.sqlite import SqliteChunkMultiVectorRepository, SqliteUnitOfWork


async def _seed_chunks(db_path: Path) -> None:
    """Insert two chunks via a UoW so chunk ids 1 and 2 exist."""
    provider = build_connection_provider(db_path)
    import asyncio

    def _insert() -> None:
        with provider.acquire_sync() as conn:
            conn.execute(
                "INSERT INTO chunks(package, title, text, origin) VALUES('p','t1','b','dep_doc')"
            )
            conn.execute(
                "INSERT INTO chunks(package, title, text, origin) VALUES('q','t2','b','dep_doc')"
            )
            conn.commit()

    await asyncio.to_thread(_insert)


@pytest.mark.asyncio
async def test_next_plaid_offset_empty_is_zero(tmp_path: Path) -> None:
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    repo = SqliteChunkMultiVectorRepository(provider=build_connection_provider(db_path))
    assert await repo.next_plaid_offset() == 0


@pytest.mark.asyncio
async def test_upsert_and_next_offset(tmp_path: Path) -> None:
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    await _seed_chunks(db_path)
    provider = build_connection_provider(db_path)
    # The repo never commits — drive commit through a UoW transaction.
    uow = SqliteUnitOfWork(provider=provider)
    async with uow:
        repo = SqliteChunkMultiVectorRepository(provider=provider)
        await repo.upsert(((1, 0, "p", "h"), (2, 1, "q", "h")))
        await uow.commit()

    repo = SqliteChunkMultiVectorRepository(provider=provider)
    # next offset = MAX(plaid_doc_id)+1 = 2
    assert await repo.next_plaid_offset() == 2
    assert await repo.packages_for_chunks([1, 2]) == {1: "p", 2: "q"}
    assert dict(await repo.plaid_ids_for_chunks([1, 2])) == {0: 1, 1: 2}


@pytest.mark.asyncio
async def test_delete_by_chunk_ids_returns_plaid_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    await _seed_chunks(db_path)
    provider = build_connection_provider(db_path)
    uow = SqliteUnitOfWork(provider=provider)
    async with uow:
        repo = SqliteChunkMultiVectorRepository(provider=provider)
        await repo.upsert(((1, 0, "p", "h"), (2, 1, "q", "h")))
        await uow.commit()

    uow = SqliteUnitOfWork(provider=provider)
    async with uow:
        repo = SqliteChunkMultiVectorRepository(provider=provider)
        deleted = await repo.delete_by_chunk_ids([1])
        await uow.commit()
    assert set(deleted) == {0}

    repo = SqliteChunkMultiVectorRepository(provider=provider)
    assert await repo.next_plaid_offset() == 2  # plaid_doc_id 1 still present
    assert await repo.packages_for_chunks([1, 2]) == {1: "p", 2: "q"}
    assert dict(await repo.plaid_ids_for_chunks([1, 2])) == {1: 2}


@pytest.mark.asyncio
async def test_clear_returns_all_plaid_ids_and_empties(tmp_path: Path) -> None:
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    await _seed_chunks(db_path)
    provider = build_connection_provider(db_path)
    uow = SqliteUnitOfWork(provider=provider)
    async with uow:
        repo = SqliteChunkMultiVectorRepository(provider=provider)
        await repo.upsert(((1, 0, "p", "h"), (2, 1, "q", "h")))
        await uow.commit()

    uow = SqliteUnitOfWork(provider=provider)
    async with uow:
        repo = SqliteChunkMultiVectorRepository(provider=provider)
        cleared = await repo.clear()
        await uow.commit()
    assert set(cleared) == {0, 1}

    repo = SqliteChunkMultiVectorRepository(provider=provider)
    assert await repo.next_plaid_offset() == 0


@pytest.mark.asyncio
async def test_empty_inputs_are_noops(tmp_path: Path) -> None:
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    provider = build_connection_provider(db_path)
    repo = SqliteChunkMultiVectorRepository(provider=provider)
    await repo.upsert(())  # no rows
    assert await repo.delete_by_chunk_ids([]) == ()
    assert await repo.packages_for_chunks([]) == {}
    assert await repo.plaid_ids_for_chunks([]) == ()
    assert await repo.next_plaid_offset() == 0


@pytest.mark.asyncio
async def test_upsert_rolls_back_with_transaction(tmp_path: Path) -> None:
    """The repo never commits on its own — a UoW rollback must drop its rows."""
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    await _seed_chunks(db_path)
    provider = build_connection_provider(db_path)
    uow = SqliteUnitOfWork(provider=provider)
    async with uow:
        repo = SqliteChunkMultiVectorRepository(provider=provider)
        await repo.upsert(((1, 0, "p", "h"),))
        # no commit -> __aexit__ rolls back
    repo = SqliteChunkMultiVectorRepository(provider=provider)
    assert await repo.next_plaid_offset() == 0


@pytest.mark.asyncio
async def test_mapping_write_shares_held_transaction_no_deadlock(tmp_path: Path) -> None:
    """Regression for the FastPlaid/SQLite deadlock — torch-free.

    The mapping write must route through the SAME connection a held SQLite
    write transaction already owns. The original bug opened a SECOND connection,
    which cannot take the write lock the open transaction holds — raising
    ``sqlite3.OperationalError: database is locked`` (or blocking, if a
    ``busy_timeout`` is ever configured). This reproduces the production
    ``SqliteUnitOfWork`` + mapping-repo shared-provider scenario WITHOUT
    fast-plaid / torch, so it runs in the default ``.venv`` (the fast-plaid
    write test is skipped there). The ``asyncio.wait_for`` is a defensive bound
    so a future ``busy_timeout`` increase can't turn the regression into an
    indefinite hang; with the fix the write completes well under it.
    """
    import asyncio

    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    await _seed_chunks(db_path)
    provider = build_connection_provider(db_path)

    async def _run() -> None:
        uow = SqliteUnitOfWork(provider=provider)
        async with uow:
            # Acquire the write lock FIRST (mirrors a composite-UoW SQLite child
            # that has already written in the open transaction)...
            await uow.chunks.delete(filter={"package": "p"})
            # ...then write the mapping through the SAME provider. A second
            # connection here would deadlock on the held write lock.
            repo = SqliteChunkMultiVectorRepository(provider=provider)
            await repo.upsert(((2, 0, "q", "h"),))
            await uow.commit()

    await asyncio.wait_for(_run(), timeout=10.0)

    repo = SqliteChunkMultiVectorRepository(provider=provider)
    assert dict(await repo.plaid_ids_for_chunks([2])) == {0: 2}
