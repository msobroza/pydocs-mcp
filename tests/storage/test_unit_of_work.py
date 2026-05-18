"""Tests for SqliteUnitOfWork + _maybe_acquire (spec §5.3 + §14.2 / §14.9)."""
from __future__ import annotations

import sqlite3

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.models import Package, PackageOrigin
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
from pydocs_mcp.storage.sqlite import (
    SqliteUnitOfWork,
    _maybe_acquire,
    _sqlite_transaction,
)


def _pkg(name: str = "x") -> Package:
    return Package(
        name=name, version="0", summary="", homepage="",
        dependencies=(), content_hash="", origin=PackageOrigin.DEPENDENCY,
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

    async with uow:
        async with _maybe_acquire(provider) as conn:
            conn.execute(
                "INSERT INTO packages (name, version, summary, homepage, "
                "dependencies, content_hash, origin) VALUES (?,?,?,?,?,?,?)",
                ("test_pkg", "1.0", "", "", "[]", "h", "dependency"),
            )
        await uow.commit()

    # After commit, the row must be visible on a fresh connection
    fresh = sqlite3.connect(str(db_file))
    count = fresh.execute("SELECT COUNT(*) FROM packages WHERE name=?", ("test_pkg",)).fetchone()[0]
    fresh.close()
    assert count == 1


async def test_unit_of_work_rollbacks_on_exception(db_file):
    provider = build_connection_provider(db_file)
    uow = SqliteUnitOfWork(provider=provider)

    with pytest.raises(RuntimeError, match="boom"):
        async with uow:
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


async def test_maybe_acquire_commits_non_uow_write(db_file):
    """Outside a UoW, ``_maybe_acquire`` commits on success so the write
    is visible to a fresh connection. Regression for the commit-fold that
    replaced the ``if _sqlite_transaction.get() is None: conn.commit()``
    gate previously duplicated across every repository write method.
    """
    provider = build_connection_provider(db_file)
    async with _maybe_acquire(provider) as conn:
        conn.execute(
            "INSERT INTO packages (name, version, summary, homepage, "
            "dependencies, content_hash, origin) VALUES (?,?,?,?,?,?,?)",
            ("committed_pkg", "1.0", "", "", "[]", "h", "dependency"),
        )

    fresh = sqlite3.connect(str(db_file))
    count = fresh.execute(
        "SELECT COUNT(*) FROM packages WHERE name=?", ("committed_pkg",),
    ).fetchone()[0]
    fresh.close()
    assert count == 1


async def test_maybe_acquire_rollbacks_non_uow_write_on_exception(db_file):
    """An exception inside ``_maybe_acquire`` (without ambient UoW) rolls
    back the in-flight write — no partial state leaks out."""
    provider = build_connection_provider(db_file)
    with pytest.raises(RuntimeError, match="boom"):
        async with _maybe_acquire(provider) as conn:
            conn.execute(
                "INSERT INTO packages (name, version, summary, homepage, "
                "dependencies, content_hash, origin) VALUES (?,?,?,?,?,?,?)",
                ("half_written", "1.0", "", "", "[]", "h", "dependency"),
            )
            raise RuntimeError("boom")

    fresh = sqlite3.connect(str(db_file))
    count = fresh.execute(
        "SELECT COUNT(*) FROM packages WHERE name=?", ("half_written",),
    ).fetchone()[0]
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

    async with uow:
        await _asyncio.gather(
            repo.upsert([_chunk("a"), _chunk("b")]),
            repo.upsert([_chunk("c"), _chunk("d")]),
        )
        await uow.commit()

    fresh = sqlite3.connect(str(db_file))
    titles = {
        r[0] for r in fresh.execute("SELECT title FROM chunks").fetchall()
    }
    fresh.close()
    assert titles == {"a", "b", "c", "d"}


# ── §14.2 + §14.9 — async context-manager shape (Task 2) ───────────────


@pytest.mark.asyncio
async def test_sqlite_uow_repos_accessible_inside_context(tmp_path):
    """§14.9 AC #2 — repo attributes valid inside async with."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()
    uow = SqliteUnitOfWork(provider=build_connection_provider(db))
    async with uow as inside:
        assert inside is uow
        assert inside.packages is not None
        assert inside.chunks is not None
        assert inside.module_members is not None
        assert inside.trees is not None
        await inside.commit()


@pytest.mark.asyncio
async def test_sqlite_uow_attribute_outside_context_raises(tmp_path):
    """§14.9 AC #7."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()
    uow = SqliteUnitOfWork(provider=build_connection_provider(db))
    with pytest.raises(UnitOfWorkNotEnteredError) as excinfo:
        _ = uow.packages
    assert excinfo.value.attr_name == "packages"


@pytest.mark.asyncio
async def test_sqlite_uow_commit_persists_across_reopen(tmp_path):
    """§14.9 AC #3 — proves the ContextVar wired through. Without the
    ContextVar set in __aenter__, the upsert commits to a transient
    connection and is not visible after reopen."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()

    uow = SqliteUnitOfWork(provider=build_connection_provider(db))
    async with uow:
        await uow.packages.upsert(_pkg("inside_uow"))
        await uow.commit()

    uow2 = SqliteUnitOfWork(provider=build_connection_provider(db))
    async with uow2:
        got = await uow2.packages.get("inside_uow")
        assert got is not None
        assert got.name == "inside_uow"


@pytest.mark.asyncio
async def test_sqlite_uow_rollback_on_exception(tmp_path):
    """§14.2 safety-net — exception triggers rollback before propagating."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()

    uow = SqliteUnitOfWork(provider=build_connection_provider(db))
    with pytest.raises(ValueError):
        async with uow:
            await uow.packages.upsert(_pkg("doomed"))
            raise ValueError("simulated")

    uow2 = SqliteUnitOfWork(provider=build_connection_provider(db))
    async with uow2:
        got = await uow2.packages.get("doomed")
        assert got is None


@pytest.mark.asyncio
async def test_sqlite_uow_rollback_when_commit_not_called(tmp_path):
    """§14.2 — exit without commit rolls back."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()

    uow = SqliteUnitOfWork(provider=build_connection_provider(db))
    async with uow:
        await uow.packages.upsert(_pkg("nocommit"))

    uow2 = SqliteUnitOfWork(provider=build_connection_provider(db))
    async with uow2:
        got = await uow2.packages.get("nocommit")
        assert got is None


# ── §14.7 — references repo attribute (sub-PR #5b, Task 7) ─────────────


@pytest.mark.asyncio
async def test_uow_references_attribute_accessible_inside_context(tmp_path):
    """spec §14.7 — references is the 5th repo attribute (sub-PR #5b)."""
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
    from pydocs_mcp.storage.sqlite import SqliteReferenceStore, SqliteUnitOfWork

    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    async with SqliteUnitOfWork(provider=provider) as uow:
        assert isinstance(uow.references, SqliteReferenceStore)


@pytest.mark.asyncio
async def test_uow_references_raises_outside_context(tmp_path):
    """spec §14.2 — outside `async with`, references @property raises."""
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
    from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
    from pydocs_mcp.storage.sqlite import SqliteUnitOfWork

    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    uow = SqliteUnitOfWork(provider=provider)
    with pytest.raises(UnitOfWorkNotEnteredError):
        _ = uow.references

