"""The refresh loop (spec §6.8, §6.8c; #317): the file watcher and the ref watcher
submit to ONE job queue, whose single worker runs every pass — a save and a ref
move never race, and passes never overlap.

``run_refresh_loop`` keeps the sources alive as long as the MCP server (or, for
the standalone ``watch``, until cancelled). ``refresh_in_background`` runs it on
a thread with its own event loop, so plain ``serve`` keeps the blocking MCP
server on the main thread, where SIGINT reaches it (CQ-1,
``tests/test_main_cli.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydocs_mcp.serve.index_jobs import (
    WORKING_TREE_PRIORITY,
    IndexJob,
    IndexJobKind,
    IndexJobQueue,
)
from pydocs_mcp.serve.ref_watcher import RefEvents
from pydocs_mcp.serve.refresh_jobs import BranchTracking, events_to_jobs

log = logging.getLogger("pydocs-mcp")

# How long the server's exit waits for the refresh thread to wind down. It
# bounds that wait only: a pass step already running in a worker thread
# (``asyncio.to_thread``) cannot be cancelled and runs to its end, and the
# interpreter's exit joins the executor's workers — so a shutdown mid-pass
# still waits for that step (#317 review).
_BACKGROUND_JOIN_SECONDS = 10.0


class RefWatchSource(Protocol):
    async def run_until_cancelled(
        self, on_events: Callable[[RefEvents], Awaitable[None]]
    ) -> None: ...


class FileWatchSource(Protocol):
    async def run_until_cancelled(self, on_change: Callable[[], Awaitable[None]]) -> None: ...


@dataclass(frozen=True, slots=True)
class RefreshSubmissions:
    """What the two watchers hand the queue."""

    queue: IndexJobQueue
    tracking: BranchTracking

    async def on_ref_events(self, events: RefEvents) -> None:
        refs = await asyncio.to_thread(self.tracking.read)
        jobs = events_to_jobs(
            events, tracked=refs.tracked, working_tree_branch=refs.working_tree_branch
        )
        for job in jobs:
            await self.queue.submit(job)

    async def on_file_change(self) -> None:
        # Keyed on the working tree's branch, so a save and a checkout of that
        # branch coalesce into one job (spec §6.8c burst table).
        name = await asyncio.to_thread(self.tracking.working_tree_branch)
        job = IndexJob(IndexJobKind.BRANCH_INDEX, name, priority=WORKING_TREE_PRIORITY)
        await self.queue.submit(job)


async def run_refresh_loop(
    submissions: RefreshSubmissions,
    *,
    ref_watcher: RefWatchSource | None,
    file_watcher: FileWatchSource | None,
    serve: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Run the queue and the watchers while ``serve`` runs. With no server, the
    loop lives as long as the file watcher (the standalone ``watch``'s lifetime
    before the queue existed), or until cancelled when there is none. Every
    source is cancelled and awaited on the way out, so no observer thread and
    no pass outlives the loop."""
    sources = _RunningSources.start(submissions, ref_watcher, file_watcher)
    try:
        await (serve() if serve is not None else sources.until_done())
    finally:
        await _stop_sources(sources.all())


@dataclass(frozen=True, slots=True)
class _RunningSources:
    queue: asyncio.Task[None]
    ref_watch: asyncio.Task[None] | None
    file_watch: asyncio.Task[None] | None

    @classmethod
    def start(
        cls,
        submissions: RefreshSubmissions,
        ref_watcher: RefWatchSource | None,
        file_watcher: FileWatchSource | None,
    ) -> _RunningSources:
        queue = asyncio.create_task(submissions.queue.run_until_cancelled(), name="index-job-queue")
        ref_watch = file_watch = None
        if ref_watcher is not None:
            watch = ref_watcher.run_until_cancelled(submissions.on_ref_events)
            ref_watch = asyncio.create_task(watch, name="ref-watcher")
        if file_watcher is not None:
            changes = file_watcher.run_until_cancelled(submissions.on_file_change)
            file_watch = asyncio.create_task(changes, name="file-watcher")
        return cls(queue, ref_watch, file_watch)

    def all(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(t for t in (self.queue, self.ref_watch, self.file_watch) if t is not None)

    async def until_done(self) -> None:
        # The ref watcher may return early (no observer could start) and the
        # queue never does: neither decides the loop's lifetime.
        if self.file_watch is not None:
            await self.file_watch
        else:
            await asyncio.gather(*self.all())


async def _stop_sources(tasks: Sequence[asyncio.Task[None]]) -> None:
    for task in tasks:
        task.cancel()
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    for task, outcome in zip(tasks, outcomes, strict=True):
        if isinstance(outcome, Exception):
            log.warning("watch: %s task exited with %s", task.get_name(), outcome)


@contextlib.contextmanager
def refresh_in_background(run: Callable[[], Awaitable[None]]) -> Iterator[None]:
    """Run ``run`` on a daemon thread with its own event loop for the ``with``
    body; on exit, cancel it and join it for at most ``_BACKGROUND_JOIN_SECONDS``.

    Cancellation lands at the pass's next ``await``: an open unit of work rolls
    back, but a blocking step already handed to a worker thread finishes first,
    and process exit waits for it.
    """
    thread = _RefreshThread(run)
    thread.start()
    try:
        yield
    finally:
        thread.stop()


class _RefreshThread:
    def __init__(self, run: Callable[[], Awaitable[None]]) -> None:
        self._run = run
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._thread = threading.Thread(target=self._main, name="pydocs-refresh", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait()

    def stop(self) -> None:
        if self._loop is not None and self._task is not None:
            # The loop may have closed already (the run returned or failed).
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._task.cancel)
        self._thread.join(_BACKGROUND_JOIN_SECONDS)

    def _main(self) -> None:
        try:
            asyncio.run(self._body())
        except Exception:
            # The server keeps serving the index it has: a refresh failure must
            # never take it down (the watch loop's own isolation rule).
            log.exception(json.dumps({"event": "refresh_loop_failed"}))
        finally:
            self._ready.set()

    async def _body(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        self._ready.set()
        with contextlib.suppress(asyncio.CancelledError):
            await self._run()


__all__ = (
    "FileWatchSource",
    "RefWatchSource",
    "RefreshSubmissions",
    "refresh_in_background",
    "run_refresh_loop",
)
