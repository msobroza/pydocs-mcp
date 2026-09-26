"""``sqlite_to_thread``: ``asyncio.to_thread`` that never abandons its worker.

A drop-in for ``asyncio.to_thread`` on work that touches a SQLite connection: the
same result, exception and context-variable behaviour, except that a cancelled
caller first waits for its worker thread, so the connection outlives its last
use (the PR #399 CI segfault). These pin that contract on plain callables; the
connection-level regression is ``tests/storage/test_sqlite_worker_cancellation.py``.
"""

from __future__ import annotations

import asyncio
import threading
from contextvars import ContextVar

import pytest

from pydocs_mcp.retrieval.pipeline.connection import sqlite_to_thread

# Bounds every wait so a regression fails in seconds instead of hanging.
_WAIT_SECONDS = 5.0
# Long enough for a cancelled caller to run ahead of its worker, if it could.
_RACE_WINDOW_SECONDS = 0.1

_REQUEST_ID: ContextVar[str] = ContextVar("_REQUEST_ID", default="unset")


async def test_it_returns_what_the_worker_returned() -> None:
    assert await sqlite_to_thread(lambda: 41 + 1) == 42


async def test_it_passes_positional_and_keyword_arguments() -> None:
    assert await sqlite_to_thread(divmod, 7, 2) == (3, 1)
    assert await sqlite_to_thread(int, "ff", base=16) == 255


async def test_the_workers_exception_reaches_its_caller() -> None:
    def fail() -> None:
        raise ValueError("no such table: branches")

    with pytest.raises(ValueError, match="no such table"):
        await sqlite_to_thread(fail)


async def test_the_worker_sees_the_callers_context_variables() -> None:
    """Parity with ``asyncio.to_thread``, which runs the worker in a context copy."""
    _REQUEST_ID.set("req-7")

    assert await sqlite_to_thread(_REQUEST_ID.get) == "req-7"


async def test_a_cancelled_caller_waits_for_its_worker_before_the_cancellation_propagates() -> None:
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def worker() -> None:
        started.set()
        release.wait(_WAIT_SECONDS)
        finished.set()

    task = asyncio.ensure_future(sqlite_to_thread(worker))
    assert await asyncio.to_thread(started.wait, _WAIT_SECONDS)
    task.cancel()
    await asyncio.sleep(_RACE_WINDOW_SECONDS)

    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_a_second_cancel_does_not_cut_the_wait_short() -> None:
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def worker() -> None:
        started.set()
        release.wait(_WAIT_SECONDS)
        finished.set()

    task = asyncio.ensure_future(sqlite_to_thread(worker))
    assert await asyncio.to_thread(started.wait, _WAIT_SECONDS)
    task.cancel()
    await asyncio.sleep(_RACE_WINDOW_SECONDS)
    task.cancel()
    await asyncio.sleep(_RACE_WINDOW_SECONDS)

    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_a_worker_that_fails_after_its_caller_was_cancelled_leaves_the_cancellation() -> None:
    """The caller was cancelled: that is what it sees, not the worker's late failure."""
    started, release = threading.Event(), threading.Event()

    def worker() -> None:
        started.set()
        release.wait(_WAIT_SECONDS)
        raise ValueError("database is locked")

    task = asyncio.ensure_future(sqlite_to_thread(worker))
    assert await asyncio.to_thread(started.wait, _WAIT_SECONDS)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
