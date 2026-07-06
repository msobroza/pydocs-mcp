"""Ambient-transaction plumbing shared by every SQLite repository.

``_sqlite_transaction`` + ``_maybe_acquire`` are the sanctioned shared
internals of the ``storage/sqlite`` package: the repositories, the
UnitOfWork, and the composition-root factories
(``storage/factories.py``) import them from here instead of reaching
across a monolithic module boundary for underscore names.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from pydocs_mcp.retrieval.protocols import ConnectionProvider

# Ambient transaction state — set by SqliteUnitOfWork.__aenter__, read by _maybe_acquire.
# The lock serialises concurrent repo calls that share the ambient connection —
# ``asyncio.gather(repo.a.upsert(...), repo.b.upsert(...))`` inside a UoW
# would otherwise race two worker threads on the same sqlite3.Connection
# (undefined behaviour: interleaved SQL / corrupted transaction state).
_sqlite_transaction: ContextVar[tuple[sqlite3.Connection, asyncio.Lock] | None] = ContextVar(
    "_sqlite_transaction",
    default=None,
)


@asynccontextmanager
async def _maybe_acquire(
    provider: ConnectionProvider,
) -> AsyncIterator[sqlite3.Connection]:
    """Reuse the ambient transaction's conn if set; otherwise acquire fresh via provider.

    When there is no ambient :class:`SqliteUnitOfWork` the context manager
    owns the commit/rollback lifecycle — successful exit commits, an
    exception triggers a rollback before re-raising. Inside a UoW scope
    the transaction is driven by :meth:`SqliteUnitOfWork.begin` and this
    helper only yields the shared connection; commit/rollback there is
    the UoW's responsibility. This folds the former
    ``if _sqlite_transaction.get() is None: conn.commit()`` gate that was
    duplicated across every repository write method.
    """
    ambient = _sqlite_transaction.get()
    if ambient is not None:
        conn, lock = ambient
        async with lock:
            yield conn
    else:
        async with provider.acquire() as conn:
            try:
                yield conn
            except BaseException:
                await asyncio.to_thread(conn.rollback)
                raise
            else:
                await asyncio.to_thread(conn.commit)
