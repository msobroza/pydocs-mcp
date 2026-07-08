"""ConnectionProvider Protocol — acquire_sync conformance (spec C4).

Pins that :class:`PerCallConnectionProvider` exposes a sync-friendly
``acquire_sync()`` context manager so retrieval steps that run inside
``asyncio.to_thread()`` can obtain a SQLite connection without nesting
an ``async with`` inside the worker thread (deadlock-prone, awkward
ergonomics).
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.retrieval.pipeline.connection import CacheNotIndexedError


def test_acquire_sync_returns_sqlite_connection(tmp_path):
    """``acquire_sync`` yields a ``sqlite3.Connection`` from a context manager."""
    db_path = tmp_path / "test.db"
    db_path.touch()
    provider = PerCallConnectionProvider(cache_path=db_path)
    with provider.acquire_sync() as conn:
        assert isinstance(conn, sqlite3.Connection)
        # PerCallConnectionProvider sets row_factory=sqlite3.Row — index by
        # position rather than comparing to a tuple literal.
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_acquire_sync_uses_check_same_thread_false(tmp_path):
    """The acquired connection is usable from a different thread.

    Retrieval steps wrap connection-using work in ``asyncio.to_thread``
    (executor thread != opening thread). The connection must be opened
    with ``check_same_thread=False`` or sqlite3 will raise
    ``ProgrammingError`` on cross-thread use.
    """
    db_path = tmp_path / "test.db"
    db_path.touch()
    provider = PerCallConnectionProvider(cache_path=db_path)
    with provider.acquire_sync() as conn:
        result: dict[str, object] = {}

        def worker() -> None:
            result["val"] = conn.execute("SELECT 2").fetchone()[0]

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert result["val"] == 2


def test_acquire_sync_closes_connection_on_exit(tmp_path):
    """Exiting the ``with`` block closes the underlying SQLite connection."""
    db_path = tmp_path / "test.db"
    db_path.touch()
    provider = PerCallConnectionProvider(cache_path=db_path)
    with provider.acquire_sync() as conn:
        captured = conn
    # SQLite raises ProgrammingError when used after close().
    with pytest.raises(sqlite3.ProgrammingError):
        captured.execute("SELECT 1")


def test_acquire_sync_missing_cache_raises_actionable_error_and_no_file(tmp_path):
    """A query against a never-indexed project must not create a stray .db.

    Regression for: sqlite3.connect(path) creates a 4096-byte empty
    database file as a side effect of merely opening a connection to a
    nonexistent path. Before this fix, that stray file then caused (a) a
    raw ``OperationalError: no such table: chunks_fts`` instead of an
    actionable "project not indexed" message, and (b) a schema-less
    sidecar left behind at ``cache_path`` that misleads any later
    existence-based "is this project indexed?" check.
    """
    cache_path = tmp_path / "never_indexed.db"
    assert not cache_path.exists()

    provider = PerCallConnectionProvider(cache_path=cache_path)
    with pytest.raises(CacheNotIndexedError, match=r"pydocs-mcp index"):
        with provider.acquire_sync() as _conn:
            pass

    # The core assertion: opening a connection to index/query a
    # never-indexed project must not fabricate an empty sidecar file.
    assert not cache_path.exists()


async def test_acquire_missing_cache_raises_actionable_error_and_no_file(tmp_path):
    """Async ``acquire()`` mirrors the sync path's missing-cache behavior."""
    cache_path = tmp_path / "never_indexed_async.db"
    assert not cache_path.exists()

    provider = PerCallConnectionProvider(cache_path=cache_path)
    with pytest.raises(CacheNotIndexedError, match=r"pydocs-mcp index"):
        async with provider.acquire() as _conn:
            pass

    assert not cache_path.exists()
