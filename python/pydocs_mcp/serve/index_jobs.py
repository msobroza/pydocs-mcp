"""One queue for every refresh source (spec §6.8c, #317).

The file watcher, the ref watcher and (later) the remote lane all submit here,
so a save and a ref move never race: at most one pending job per key, one
parked follow-up per running key, serial execution under one lock, priority
order, failure isolation. A job is a *request* for a pass; the pass itself is
content-addressed, so the number of events never changes the amount of work.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum

log = logging.getLogger("pydocs-mcp")

# Spec §6.8c order: the working-tree branch first (the developer's active
# context), then other local branches FIFO, then remote-derived jobs (§6.8b),
# then the maintenance jobs.
WORKING_TREE_PRIORITY = 0
LOCAL_BRANCH_PRIORITY = 1
REMOTE_PRIORITY = 2
MAINTENANCE_PRIORITY = 3


class IndexJobKind(StrEnum):
    """What a queued job asks for (spec §6.8)."""

    BRANCH_INDEX = "branch_index"
    MERGE_BASE_RECHECK = "merge_base_recheck"
    RETENTION_WINDOW = "retention_window"
    DIFF_SLICE = "diff_slice"


JobKey = tuple[IndexJobKind, str]


@dataclass(frozen=True, slots=True)
class IndexJob:
    """One requested pass. ``changed_paths`` empty means manifest-level (the
    whole branch); ``sequence`` is the FIFO tiebreak the queue stamps.

    ``ref_head_sha`` is the head the ref watcher saw, on a job only ref events
    asked for: the runner drops it when the served row already carries that
    head (#317 — a checkout's file job ran that pass first). ``None`` — a save,
    or any merge with one — always runs: a save may hold edits no pass has read.
    """

    kind: IndexJobKind
    branch: str = ""
    changed_paths: frozenset[str] = frozenset()
    priority: int = LOCAL_BRANCH_PRIORITY
    sequence: int = 0
    ref_head_sha: str | None = None

    @property
    def key(self) -> JobKey:
        return self.kind, self.branch

    def merged_with(self, other: IndexJob) -> IndexJob:
        """Union of paths; a manifest-level job (no paths) absorbs a path-level
        one. Keeps this job's place in the FIFO order and the higher priority;
        ``other`` came later, so its ref state is the newer one."""
        paths = (
            self.changed_paths | other.changed_paths
            if self.changed_paths and other.changed_paths
            else frozenset()
        )
        ref_head = None if self.ref_head_sha is None else other.ref_head_sha
        return replace(
            self,
            changed_paths=paths,
            priority=min(self.priority, other.priority),
            ref_head_sha=ref_head,
        )


class IndexJobQueue:
    """Coalescing job queue with one runner at a time (spec §6.8c).

    Not a dataclass: its state is private and mutable, and every method runs
    on the one event loop that owns it.
    """

    def __init__(self, runner: Callable[[IndexJob], Awaitable[object]]) -> None:
        self.runner = runner
        self._pending: dict[JobKey, IndexJob] = {}
        # A job for a key that is running waits here: it must see the running
        # pass's commit, and it runs once with everything that arrived meanwhile
        # (the ``deferred_paths`` semantic of the file watcher, kept).
        self._parked: dict[JobKey, IndexJob] = {}
        self._running: JobKey | None = None
        # WHY a lock although one worker runs: it is what makes "passes never
        # overlap" true by construction, even with a second drain loop.
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._sequence = 0

    async def submit(self, job: IndexJob) -> None:
        """Queue ``job``, merged into the pending (or parked) job of its key."""
        self._sequence += 1
        stamped = replace(job, sequence=self._sequence)
        target = self._parked if self._running == stamped.key else self._pending
        previous = target.get(stamped.key)
        target[stamped.key] = previous.merged_with(stamped) if previous else stamped
        self._idle.clear()
        self._wake.set()

    def snapshot(self) -> tuple[IndexJob, ...]:
        """The pending jobs in run order (parked follow-ups are not pending yet)."""
        return tuple(sorted(self._pending.values(), key=lambda j: (j.priority, j.sequence)))

    async def wait_idle(self) -> None:
        """Return once nothing is pending, parked or running."""
        if self._pending or self._parked or self._running is not None:
            await self._idle.wait()

    async def run_until_cancelled(self) -> None:
        while True:
            if not await self._run_next():
                await self._wait_for_work()

    async def _run_next(self) -> bool:
        async with self._lock:
            ordered = self.snapshot()
            if not ordered:
                return False
            await self._run_one(self._pending.pop(ordered[0].key))
            return True

    async def _wait_for_work(self) -> None:
        # No await between the empty check and the clear: a submit cannot slip
        # in between, so no wake-up is lost.
        if not self._pending and self._running is None:
            self._idle.set()
        self._wake.clear()
        await self._wake.wait()

    async def _run_one(self, job: IndexJob) -> None:
        self._running = job.key
        try:
            await self.runner(job)
        except Exception:
            # A failed pass must not stop the drain (the ``_drain_guarded``
            # precedent in serve/watcher.py): the next event retries.
            payload = {"event": "index_job_failed", "kind": job.kind.value, "branch": job.branch}
            log.exception(json.dumps(payload))
        finally:
            self._running = None
            parked = self._parked.pop(job.key, None)
            if parked is not None:
                self._pending[job.key] = parked
                self._wake.set()


__all__ = (
    "LOCAL_BRANCH_PRIORITY",
    "MAINTENANCE_PRIORITY",
    "REMOTE_PRIORITY",
    "WORKING_TREE_PRIORITY",
    "IndexJob",
    "IndexJobKind",
    "IndexJobQueue",
    "JobKey",
)
