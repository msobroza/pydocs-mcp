"""PerCallConnectionProvider — default ``ConnectionProvider`` implementation.

Opens a fresh SQLite connection per ``acquire()`` call with WAL journaling +
NORMAL synchronous, suitable for the FTS5 read paths consumed by the
retrieval pipeline. The provider is a small concrete adapter — not part of
the protocol churn — so it lives next to the pipeline base classes rather
than in any deletable "legacy" module.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ParamSpec, TypeVar

from pydocs_mcp.exceptions import PydocsMCPError

_P = ParamSpec("_P")
_T = TypeVar("_T")


async def sqlite_to_thread(func: Callable[_P, _T], /, *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """``asyncio.to_thread`` for work on a SQLite connection — never abandons its worker.

    WHY (the PR #399 CI segfault, run 36235360004): a cancelled
    ``asyncio.to_thread`` returns to its caller at once while the thread keeps
    running, so a caller holding a SQLite connection went on to roll it back and
    close it on OTHER threads while the first was still stepping it — a
    concurrent close + execute that segfaults the sqlite3 C module. Here a
    cancelled caller first waits for its worker, so the connection outlives its
    last use; the cancellation still propagates, just after the thread is done.

    The worker is a bare executor future, not a task, so neither a second cancel
    nor the event loop's cancel-all at shutdown can abandon it either. Same
    arguments, result, exceptions and context copy as ``asyncio.to_thread``.

    Example:
        >>> rows = await sqlite_to_thread(lambda: conn.execute(sql).fetchall())  # doctest: +SKIP
    """
    context = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    worker = loop.run_in_executor(None, lambda: context.run(func, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await _wait_out(worker)
        raise


async def _wait_out(worker: asyncio.Future[_T]) -> None:
    """Wait until ``worker`` finishes; a repeated cancel cannot cut the wait short.

    ``asyncio.wait`` neither cancels the future nor raises its exception, and the
    outcome is moot once the caller is cancelled — it is only marked retrieved,
    so a worker that failed late never logs "exception was never retrieved".
    """
    while not worker.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait({worker})
    if not worker.cancelled():
        worker.exception()


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
        # sqlite_to_thread, never asyncio.to_thread: every worker on this
        # connection is waited out before ``close`` runs (see that helper's WHY).
        connection = await sqlite_to_thread(self._open)
        try:
            yield connection
        finally:
            await sqlite_to_thread(connection.close)

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


__all__ = ("CacheNotIndexedError", "PerCallConnectionProvider", "sqlite_to_thread")
