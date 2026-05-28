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
        # check_same_thread=False is REQUIRED — both ``acquire`` (whose
        # close() runs through ``asyncio.to_thread``) and ``acquire_sync``
        # (called from retrieval steps' own ``asyncio.to_thread`` workers)
        # cross the opening thread.
        conn = sqlite3.connect(str(self.cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn


__all__ = ("PerCallConnectionProvider",)
