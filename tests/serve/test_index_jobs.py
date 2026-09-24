"""One queue for every refresh source (spec §6.8c, #317): coalescing per key, the
parked follow-up, priority order, failure isolation, and one worker — passes
never overlap."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from pydocs_mcp.serve.index_jobs import (
    LOCAL_BRANCH_PRIORITY,
    MAINTENANCE_PRIORITY,
    WORKING_TREE_PRIORITY,
    IndexJob,
    IndexJobKind,
    IndexJobQueue,
)

BRANCH = IndexJobKind.BRANCH_INDEX
_STUCK_AFTER_SECONDS = 5.0


class RecordingRunner:
    """Records every job the queue hands it, in order."""

    def __init__(self) -> None:
        self.ran: list[IndexJob] = []

    async def __call__(self, job: IndexJob) -> None:
        self.ran.append(job)


async def _drain(queue: IndexJobQueue) -> None:
    worker = asyncio.create_task(queue.run_until_cancelled())
    await queue.wait_idle()
    worker.cancel()


def test_a_path_level_job_merges_paths_and_a_manifest_level_one_absorbs_them() -> None:
    a = IndexJob(BRANCH, "main", frozenset({"a.py"}), priority=LOCAL_BRANCH_PRIORITY)
    b = IndexJob(BRANCH, "main", frozenset({"b.py"}), priority=WORKING_TREE_PRIORITY)
    assert a.merged_with(b) == IndexJob(BRANCH, "main", frozenset({"a.py", "b.py"}), priority=0)
    whole = IndexJob(BRANCH, "main")
    assert a.merged_with(whole).changed_paths == frozenset()
    assert whole.merged_with(a).changed_paths == frozenset()
    assert a.key == (BRANCH, "main")


def test_a_ref_job_keeps_the_newest_head_and_a_file_job_makes_the_merge_unconditional() -> None:
    """#317: a ref-driven job carries the head the ref watcher saw, so the runner
    can drop it once the served row carries that head; a save merged into it may
    hold edits no pass has read, so the merge always runs."""
    first = IndexJob(BRANCH, "main", ref_head_sha="a" * 40)
    later = IndexJob(BRANCH, "main", ref_head_sha="b" * 40)
    save = IndexJob(BRANCH, "main")
    assert first.merged_with(later).ref_head_sha == "b" * 40
    assert first.merged_with(save).ref_head_sha is None
    assert save.merged_with(first).ref_head_sha is None


async def test_pending_jobs_coalesce_per_key_and_manifest_level_absorbs_paths() -> None:
    runner = RecordingRunner()
    queue = IndexJobQueue(runner)
    await queue.submit(IndexJob(BRANCH, "main", frozenset({"a.py"}), priority=0))
    await queue.submit(IndexJob(BRANCH, "main", frozenset({"b.py"}), priority=0))
    await queue.submit(IndexJob(BRANCH, "feature/x", priority=1))
    await queue.submit(IndexJob(BRANCH, "feature/x", frozenset({"c.py"}), priority=1))
    assert [(j.branch, set(j.changed_paths)) for j in queue.snapshot()] == [
        ("main", {"a.py", "b.py"}),
        ("feature/x", set()),
    ]
    await _drain(queue)
    assert [j.branch for j in runner.ran] == ["main", "feature/x"]


async def test_the_same_branch_under_another_kind_is_another_job() -> None:
    queue = IndexJobQueue(RecordingRunner())
    await queue.submit(IndexJob(BRANCH, "main"))
    await queue.submit(IndexJob(IndexJobKind.MERGE_BASE_RECHECK, priority=MAINTENANCE_PRIORITY))
    await queue.submit(IndexJob(IndexJobKind.MERGE_BASE_RECHECK, priority=MAINTENANCE_PRIORITY))
    assert [j.key for j in queue.snapshot()] == [
        (BRANCH, "main"),
        (IndexJobKind.MERGE_BASE_RECHECK, ""),
    ]


async def test_running_key_parks_one_follow_up_and_priority_orders_the_rest() -> None:
    started, release = asyncio.Event(), asyncio.Event()
    ran: list[tuple[str, frozenset[str]]] = []

    async def runner(job: IndexJob) -> None:
        ran.append((job.branch, job.changed_paths))
        if job.branch == "main" and not started.is_set():
            started.set()
            await release.wait()

    queue = IndexJobQueue(runner)
    worker = asyncio.create_task(queue.run_until_cancelled())
    await queue.submit(IndexJob(BRANCH, "main", priority=0))
    await started.wait()
    await queue.submit(IndexJob(BRANCH, "main", frozenset({"x.py"}), priority=0))
    await queue.submit(IndexJob(BRANCH, "main", frozenset({"y.py"}), priority=0))
    await queue.submit(IndexJob(IndexJobKind.MERGE_BASE_RECHECK, priority=3))
    await queue.submit(IndexJob(BRANCH, "feature/x", priority=1))
    # The follow-up waits for its branch's running job: it is not pending yet.
    assert [j.branch for j in queue.snapshot()] == ["feature/x", ""]
    release.set()
    await queue.wait_idle()
    worker.cancel()
    assert ran == [
        ("main", frozenset()),
        ("main", frozenset({"x.py", "y.py"})),
        ("feature/x", frozenset()),
        ("", frozenset()),
    ]


async def test_passes_never_overlap() -> None:
    """A job starts only after the previous one returned, whatever was submitted
    meanwhile — even with two drain loops started by mistake (SQLite is a single
    writer, the ``.tq`` commit is whole-file — spec §6.8c)."""
    active, overlaps = 0, 0
    order: list[str] = []

    async def runner(job: IndexJob) -> None:
        nonlocal active, overlaps
        active += 1
        overlaps = max(overlaps, active)
        order.append(job.branch)
        await asyncio.sleep(0)  # yield inside the pass, where a second worker would slip in
        active -= 1

    queue = IndexJobQueue(runner)
    workers = [asyncio.create_task(queue.run_until_cancelled()) for _ in range(2)]
    for name in ("a", "b", "c", "d"):
        await queue.submit(IndexJob(BRANCH, name))
    await queue.wait_idle()
    for worker in workers:
        worker.cancel()
    assert (overlaps, order) == (1, ["a", "b", "c", "d"])


async def test_a_failing_job_never_stops_the_drain(caplog: pytest.LogCaptureFixture) -> None:
    ran: list[str] = []

    async def runner(job: IndexJob) -> None:
        if job.branch == "bad":
            raise RuntimeError("boom")
        ran.append(job.branch)

    queue = IndexJobQueue(runner)
    with caplog.at_level(logging.ERROR, logger="pydocs-mcp"):
        await queue.submit(IndexJob(BRANCH, "bad", priority=1))
        await queue.submit(IndexJob(BRANCH, "good", priority=1))
        await _drain(queue)
    assert ran == ["good"]
    failed = [json.loads(r.getMessage()) for r in caplog.records]
    assert failed == [{"event": "index_job_failed", "kind": "branch_index", "branch": "bad"}]


async def test_a_job_submitted_after_the_queue_went_idle_still_runs() -> None:
    """The lost wake-up: the worker has drained its first job and is parked on
    its wake event before the late job arrives — only the submit can wake it."""
    runner = RecordingRunner()
    queue = IndexJobQueue(runner)
    worker = asyncio.create_task(queue.run_until_cancelled())
    await queue.submit(IndexJob(BRANCH, "first"))
    await queue.wait_idle()  # it ran; the worker is now waiting for work
    await queue.submit(IndexJob(BRANCH, "late"))
    # A safety net for a lost wake-up, never a synchronization device.
    await asyncio.wait_for(queue.wait_idle(), _STUCK_AFTER_SECONDS)
    worker.cancel()
    assert [j.branch for j in runner.ran] == ["first", "late"]
