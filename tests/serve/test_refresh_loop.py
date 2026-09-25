"""The refresh loop (spec §6.8, §6.8c; #317): both watchers submit to ONE queue,
the loop lives as long as the server (or until cancelled), and plain ``serve``
runs it on a thread of its own so the MCP server keeps the main thread."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config.git_models import GitBranchesConfig
from pydocs_mcp.serve import refresh_loop
from pydocs_mcp.serve.index_jobs import IndexJob, IndexJobKind, IndexJobQueue
from pydocs_mcp.serve.ref_watcher import RefEvent, RefEventKind
from pydocs_mcp.serve.refresh_jobs import BranchTracking
from pydocs_mcp.serve.refresh_loop import (
    RefreshSubmissions,
    refresh_in_background,
    run_refresh_loop,
)

A = "a" * 40
BRANCH = IndexJobKind.BRANCH_INDEX
# A bounded wait on an event a background thread sets — never a timing assumption.
_THREAD_DEADLINE_SECONDS = 5.0


def _tracking(tmp_path: Path, head: str = "ref: refs/heads/main") -> BranchTracking:
    gitdir = tmp_path / ".git"
    for name in ("main", "feature/x"):
        ref = gitdir / "refs" / "heads" / name
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(A + "\n", encoding="utf-8")
    (gitdir / "HEAD").write_text(head + "\n", encoding="utf-8")
    return BranchTracking(gitdir, GitBranchesConfig())


async def _noop(job: IndexJob) -> None:
    return None


async def test_a_save_and_a_checkout_of_the_same_branch_coalesce(tmp_path: Path) -> None:
    """Spec §6.8c burst table: the file watcher's job is keyed on the working
    tree's branch, so file and ref events of one checkout make one job."""
    queue = IndexJobQueue(_noop)
    submissions = RefreshSubmissions(queue, _tracking(tmp_path, "ref: refs/heads/feature/x"))
    await submissions.on_file_change()
    await submissions.on_ref_events((RefEvent(RefEventKind.HEAD_MOVED, "feature/x", None),))
    assert queue.snapshot() == (IndexJob(BRANCH, "feature/x", priority=0, sequence=1),)


async def test_ref_events_submit_the_jobs_the_table_names(tmp_path: Path) -> None:
    queue = IndexJobQueue(_noop)
    submissions = RefreshSubmissions(queue, _tracking(tmp_path))
    await submissions.on_ref_events(
        (
            RefEvent(RefEventKind.BRANCH_MOVED, "main", A),
            RefEvent(RefEventKind.BRANCH_MOVED, "feature/x", A),  # not tracked by default
            RefEvent(RefEventKind.BASE_TIP_MOVED, "origin/main", A),
        )
    )
    assert [j.key for j in queue.snapshot()] == [
        (BRANCH, "main"),
        (IndexJobKind.MERGE_BASE_RECHECK, ""),
    ]


class _Source:
    """A watcher stand-in: records its start and its cancellation."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False
        self.callback: object = None

    async def run_until_cancelled(self, callback) -> None:
        self.callback = callback
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def test_the_sources_live_as_long_as_the_server(tmp_path: Path) -> None:
    submissions = RefreshSubmissions(IndexJobQueue(_noop), _tracking(tmp_path))
    ref_watcher, file_watcher = _Source(), _Source()

    async def serve() -> None:
        await ref_watcher.started.wait()
        await file_watcher.started.wait()

    await run_refresh_loop(
        submissions, ref_watcher=ref_watcher, file_watcher=file_watcher, serve=serve
    )
    assert (ref_watcher.cancelled, file_watcher.cancelled) == (True, True)
    assert ref_watcher.callback == submissions.on_ref_events
    assert file_watcher.callback == submissions.on_file_change


async def test_without_a_server_the_loop_runs_until_cancelled(tmp_path: Path) -> None:
    ran: list[IndexJob] = []

    async def runner(job: IndexJob) -> None:
        ran.append(job)

    queue = IndexJobQueue(runner)
    submissions = RefreshSubmissions(queue, _tracking(tmp_path))
    file_watcher = _Source()
    loop = asyncio.create_task(
        run_refresh_loop(submissions, ref_watcher=None, file_watcher=file_watcher)
    )
    await file_watcher.started.wait()
    await submissions.on_file_change()
    await queue.wait_idle()
    loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop
    assert [(j.branch, j.priority) for j in ran] == [("main", 0)]
    assert file_watcher.cancelled is True


class _Returns(_Source):
    async def run_until_cancelled(self, callback) -> None:
        self.callback = callback


async def test_without_a_server_the_loop_ends_with_its_file_watcher(tmp_path: Path) -> None:
    """The standalone ``watch`` lives as long as its file watcher, as it did
    before the queue existed; the queue and the ref watcher go with it."""
    submissions = RefreshSubmissions(IndexJobQueue(_noop), _tracking(tmp_path))
    ref_watcher = _Source()
    await run_refresh_loop(submissions, ref_watcher=ref_watcher, file_watcher=_Returns())
    assert ref_watcher.cancelled is True


class _Crashes(_Source):
    async def run_until_cancelled(self, callback) -> None:
        raise OSError("inotify instance limit reached")


async def test_a_crashed_source_is_logged_when_the_server_exits(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    submissions = RefreshSubmissions(IndexJobQueue(_noop), _tracking(tmp_path))

    async def serve() -> None:
        await asyncio.sleep(0)  # let the crashed task finish before shutdown

    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await run_refresh_loop(submissions, ref_watcher=None, file_watcher=_Crashes(), serve=serve)
    assert "inotify instance limit reached" in caplog.text


def test_the_background_refresh_runs_beside_the_caller_and_stops_with_it() -> None:
    """Plain ``serve`` keeps the MCP server on the main thread (SIGINT, CQ-1)
    and the refresh loop on its own thread and event loop."""
    started, cancelled = threading.Event(), threading.Event()
    threads: list[threading.Thread] = []

    async def run() -> None:
        threads.append(threading.current_thread())
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with refresh_in_background(run):
        assert started.wait(_THREAD_DEADLINE_SECONDS)
    assert cancelled.is_set()
    assert threads[0] is not threading.main_thread() and not threads[0].is_alive()


def test_the_server_exit_waits_a_bounded_time_and_leaves_a_blocking_step_to_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling the loop cannot stop a pass step already handed to a worker
    thread (``asyncio.to_thread``): the ``with`` exit waits at most the join
    bound, and the step runs to its end — interpreter exit then waits for it,
    since the executor's workers are joined at exit (#317 review)."""
    monkeypatch.setattr(refresh_loop, "_BACKGROUND_JOIN_SECONDS", 0.05)
    in_step, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocking_step() -> None:
        in_step.set()
        release.wait(_THREAD_DEADLINE_SECONDS)
        finished.set()

    async def run() -> None:
        await asyncio.to_thread(blocking_step)

    with refresh_in_background(run):
        assert in_step.wait(_THREAD_DEADLINE_SECONDS)
    assert not finished.is_set()  # the exit did not wait for the step
    release.set()
    assert finished.wait(_THREAD_DEADLINE_SECONDS)  # nor was it killed


def test_a_failing_background_refresh_never_reaches_the_server(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        raise RuntimeError("no index")

    with caplog.at_level(logging.ERROR, logger="pydocs-mcp"), refresh_in_background(run):
        pass
    assert "refresh_loop_failed" in caplog.text
