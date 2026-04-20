"""SQLite storage adapters — UnitOfWork, Repositories, VectorStore, FilterAdapter."""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from pydocs_mcp.retrieval.protocols import ConnectionProvider

# Ambient transaction connection — set by SqliteUnitOfWork.begin, read by _maybe_acquire.
_sqlite_transaction: ContextVar[sqlite3.Connection | None] = ContextVar(
    "_sqlite_transaction", default=None,
)


@asynccontextmanager
async def _maybe_acquire(
    provider: ConnectionProvider,
) -> AsyncIterator[sqlite3.Connection]:
    """Reuse the ambient transaction's conn if set; otherwise acquire fresh via provider."""
    ambient = _sqlite_transaction.get()
    if ambient is not None:
        yield ambient
    else:
        async with provider.acquire() as conn:
            yield conn


@dataclass(frozen=True, slots=True)
class SqliteUnitOfWork:
    """Atomic transaction scope spanning multiple repository operations (spec §5.3)."""

    provider: ConnectionProvider

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        async with self.provider.acquire() as conn:
            await asyncio.to_thread(conn.execute, "BEGIN")
            token = _sqlite_transaction.set(conn)
            try:
                yield
            except BaseException:
                await asyncio.to_thread(conn.rollback)
                raise
            else:
                await asyncio.to_thread(conn.commit)
            finally:
                _sqlite_transaction.reset(token)
