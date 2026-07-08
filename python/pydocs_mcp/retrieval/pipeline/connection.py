"""PerCallConnectionProvider — default ``ConnectionProvider`` implementation.

Opens a fresh SQLite connection per ``acquire()`` call with WAL journaling +
NORMAL synchronous, suitable for the FTS5 read paths consumed by the
retrieval pipeline. The provider is a small concrete adapter — not part of
the protocol churn — so it lives next to the pipeline base classes rather
than in any deletable "legacy" module.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.exceptions import PydocsMCPError


class CacheNotIndexedError(PydocsMCPError, FileNotFoundError):
    """Raised when a query targets a project that has never been indexed.

    ``sqlite3.connect(path)`` silently creates an empty 4096-byte database
    file as a side effect of opening a connection to a nonexistent path —
    without this guard, a query against a never-indexed project (a) fails
    deep inside the FTS layer with a raw and unhelpful
    ``OperationalError: no such table: chunks_fts``, and (b) leaves behind
    a schema-less sidecar at ``cache_path`` that later existence-based
    "is this project indexed?" checks (or a human inspecting the cache
    dir) would misread as evidence of a real index. Raised BEFORE
    ``sqlite3.connect`` so the file is never created.
    """

    def __init__(self, cache_path: Path) -> None:
        super().__init__(
            f"{cache_path} does not exist — this project has not been "
            "indexed yet. Run `pydocs-mcp index <path>` first.",
        )
        self.cache_path = cache_path


@dataclass(frozen=True, slots=True)
class PerCallConnectionProvider:
    """Default ConnectionProvider — opens/closes a fresh SQLite conn per acquire()."""

    cache_path: Path

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[sqlite3.Connection]:
        connection = await asyncio.to_thread(self._open)
        try:
            yield connection
        finally:
            await asyncio.to_thread(connection.close)

    @contextmanager
    def acquire_sync(self) -> Iterator[sqlite3.Connection]:
        """Sync mirror of :meth:`acquire` (spec C4 — formalized Protocol surface).

        Used by retrieval steps that already hand work off to
        ``asyncio.to_thread`` and would otherwise need to wrap the async
        CM inside the worker thread. The connection is opened with
        ``check_same_thread=False`` (via :meth:`_open`) so the executor
        thread can use it.
        """
        connection = self._open()
        try:
            yield connection
        finally:
            connection.close()

    def _open(self) -> sqlite3.Connection:
        # Stat before connect: sqlite3.connect() silently creates an empty
        # DB file when cache_path doesn't exist, which both masks a
        # never-indexed project behind a confusing FTS OperationalError
        # and leaves a schema-less stray sidecar on disk. Raise the
        # actionable error before that side effect can happen.
        if not self.cache_path.exists():
            raise CacheNotIndexedError(self.cache_path)
        # check_same_thread=False is REQUIRED — both ``acquire`` (whose
        # close() runs through ``asyncio.to_thread``) and ``acquire_sync``
        # (called from retrieval steps' own ``asyncio.to_thread`` workers)
        # cross the opening thread.
        conn = sqlite3.connect(str(self.cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            # A read-only cache path (CI image baking a pre-built index, a
            # shared ~/.pydocs-mcp with restrictive permissions, ...) can't
            # create the -wal/-shm sidecars WAL needs, so this pragma raises
            # "attempt to write a readonly database" before any query runs —
            # even though pure reads against an already-committed db need no
            # write access at all. Degrade to the connection's existing
            # journal mode instead of failing every read on a read-only
            # deployment that never asked to write.
            pass
        return conn


__all__ = ("CacheNotIndexedError", "PerCallConnectionProvider")
