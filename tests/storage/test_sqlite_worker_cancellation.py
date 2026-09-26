"""A cancelled SQLite call never releases its connection under a running worker.

Regression for the segfault in PR #399's CI (run 36235360004, job 108386033091):
``tests/integration/test_remote_lane.py`` cancelled a refresh loop while
``SqliteBranchRepository.list_branches`` was reading on a worker thread.
``asyncio.to_thread`` returns to a cancelled caller at once, but its thread keeps
running, so ``_maybe_acquire`` rolled back and ``PerCallConnectionProvider``
closed that connection on OTHER threads while the first was still stepping it —
a concurrent close + execute that segfaults the sqlite3 C module.

The read is paused between two SQLite calls, outside SQLite's own mutex, so the
race is shown deterministically and without crashing: the buggy path releases
the connection while the paused worker still holds it; the fixed one waits.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.storage.branch_records import BranchIndexSource, BranchRecord
from pydocs_mcp.storage.sqlite import SqliteBranchRepository, SqliteUnitOfWork

# Long enough for a racy release to happen if the code under test allows one.
_RACE_WINDOW_SECONDS = 0.2
# A bound on every wait, so a regression hangs for seconds, never forever.
_WAIT_SECONDS = 5.0


@dataclass
class ReadPause:
    """The cues one paused read and the test exchange across threads."""

    in_read: threading.Event = field(default_factory=threading.Event)
    resume: threading.Event = field(default_factory=threading.Event)
    events: list[str] = field(default_factory=list)


@dataclass
class PausingCursor:
    """A real cursor whose ``fetchall`` stops, on cue, before its next SQLite call."""

    real: sqlite3.Cursor
    pause: ReadPause

    def fetchall(self) -> list[Any]:
        self.pause.in_read.set()
        self.pause.resume.wait(_WAIT_SECONDS)
        rows = self.real.fetchall()
        self.pause.events.append("read finished")
        return rows


@dataclass
class PausingConnection:
    """A real per-call connection whose reads pause mid-fetch; all else delegates."""

    real: sqlite3.Connection
    pause: ReadPause

    def execute(self, sql: str, params: Sequence[object] = ()) -> PausingCursor:
        return PausingCursor(self.real.execute(sql, params), self.pause)

    def commit(self) -> None:
        self.real.commit()

    def rollback(self) -> None:
        self.real.rollback()


@dataclass(frozen=True)
class PausingConnectionProvider:
    """The REAL per-call provider, with the release point recorded and reads pausable."""

    inner: PerCallConnectionProvider
    pause: ReadPause

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[PausingConnection]:
        async with self.inner.acquire() as real:
            try:
                yield PausingConnection(real, self.pause)
            finally:
                self.pause.events.append("released")


def _indexed_db(tmp_path: Path) -> PerCallConnectionProvider:
    """A real index database holding one branch row."""
    db = tmp_path / "index.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    record = BranchRecord(
        name="main",
        head_sha="a" * 40,
        source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p",
        indexed_at=1.0,
        last_used_at=1.0,
        is_default=True,
    )
    asyncio.run(SqliteBranchRepository(provider=provider).upsert_branch(record))
    return provider


async def _cancel_mid_read(read: Callable[[], Awaitable[object]], pause: ReadPause) -> None:
    """Cancel ``read`` while its worker is paused mid-fetch; assert the release waits."""
    task = asyncio.ensure_future(read())
    assert await asyncio.to_thread(pause.in_read.wait, _WAIT_SECONDS)
    task.cancel()
    await asyncio.sleep(_RACE_WINDOW_SECONDS)
    # The worker still holds the connection: nothing may have released it yet.
    assert pause.events == []
    pause.resume.set()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("the cancelled read completed instead of being cancelled")
    assert pause.events == ["read finished", "released"]


def test_a_cancelled_repository_read_releases_its_connection_after_the_worker(
    tmp_path: Path,
) -> None:
    """The crash site itself: ``list_branches`` over a real per-call connection."""
    pause = ReadPause()
    provider = PausingConnectionProvider(_indexed_db(tmp_path), pause)
    repository = SqliteBranchRepository(provider=provider)  # type: ignore[arg-type]

    asyncio.run(_cancel_mid_read(repository.list_branches, pause))


def test_a_cancelled_read_inside_a_unit_of_work_releases_the_held_connection_after_it(
    tmp_path: Path,
) -> None:
    """The held-connection path: the UoW rolls back and closes only after the worker."""
    pause = ReadPause()
    provider = PausingConnectionProvider(_indexed_db(tmp_path), pause)

    async def read_inside_a_unit_of_work() -> object:
        async with SqliteUnitOfWork(provider=provider) as uow:  # type: ignore[arg-type]
            return await uow.branches.list_branches()

    asyncio.run(_cancel_mid_read(read_inside_a_unit_of_work, pause))
