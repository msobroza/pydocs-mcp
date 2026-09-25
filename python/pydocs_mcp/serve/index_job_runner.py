"""The index job queue's one worker (spec §6.8, #317): which pass each job runs.

- ``BRANCH_INDEX`` of the working-tree branch → the working-tree pass (the same
  pass a file save runs; P2 makes it incremental), unless the job came from ref
  events only and the served row already carries the head they saw;
- ``BRANCH_INDEX`` of another tracked local branch → a git-objects pass
  (#310's ``BranchIndexer``);
- ``MERGE_BASE_RECHECK`` → the base re-stamp plus the #316 maintenance;
- ``RETENTION_WINDOW`` / ``DIFF_SLICE`` → logged: landing units carry no diff
  slice until P2 generates them.

The working tree, the tracked set and the served row are re-read (plumbing and
one SQLite read, no subprocess) when a job runs, not when it was queued: a job
may wait behind a checkout, or behind the pass that already did its work.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from pydocs_mcp.application.extra_branch_passes import BranchRefIndexer, run_watched_branch_pass
from pydocs_mcp.application.served_branch_head import ServedBranchHead
from pydocs_mcp.serve.index_jobs import IndexJob, IndexJobKind
from pydocs_mcp.serve.refresh_jobs import BranchTracking

log = logging.getLogger("pydocs-mcp")

# Built on first use; the FTS rebuild rides with it (it follows the pass's chunk moves).
IndexerWithRebuild = tuple[BranchRefIndexer, Callable[[], Awaitable[None]]]


class IndexJobSkipReason(StrEnum):
    """Why a queued ``BRANCH_INDEX`` runs no pass."""

    # Neither the working tree nor tracked by the time it runs: a save queued
    # just before a checkout names the branch that was left.
    UNTRACKED = "untracked"
    # Asked for by ref events only, and the served row already carries the head
    # they saw: the checkout's file job, or the startup pass, indexed it (#317).
    ALREADY_SERVED = "already_served"


def _log_skipped(name: str, reason: IndexJobSkipReason) -> None:
    log.info(json.dumps({"event": "index_job_skipped", "branch": name, "reason": reason.value}))


@dataclass(frozen=True, slots=True, kw_only=True)
class IndexJobRunner:
    """Runs one queued job; the queue guarantees one at a time."""

    tracking: BranchTracking
    reindex_working_tree: Callable[[], Awaitable[object]]
    index_other_branch: Callable[[str], Awaitable[object]]
    recheck_merge_bases: Callable[[], Awaitable[object]]
    # The served row's name and head (a SQLite read, never git).
    served_head: Callable[[], Awaitable[ServedBranchHead | None]]

    async def __call__(self, job: IndexJob) -> None:
        if job.kind is IndexJobKind.BRANCH_INDEX:
            await self._index_branch(job)
        elif job.kind is IndexJobKind.MERGE_BASE_RECHECK:
            await self.recheck_merge_bases()
        else:
            reason = "landing units carry no diff slice yet"
            payload = {"event": "index_job_deferred", "kind": job.kind.value, "reason": reason}
            log.info(json.dumps(payload))

    async def _index_branch(self, job: IndexJob) -> None:
        refs = await asyncio.to_thread(self.tracking.read)
        if job.branch == refs.working_tree_branch:
            await self._index_working_tree(job)
        elif job.branch in refs.tracked:
            await self.index_other_branch(job.branch)
        else:
            _log_skipped(job.branch, IndexJobSkipReason.UNTRACKED)

    async def _index_working_tree(self, job: IndexJob) -> None:
        if await self._already_served(job):
            _log_skipped(job.branch, IndexJobSkipReason.ALREADY_SERVED)
            return
        await self.reindex_working_tree()

    async def _already_served(self, job: IndexJob) -> bool:
        """Spec §6.8c's burst table: a checkout's file and ref events are one pass.
        The file watcher's quiet period is the shorter one, so its job usually
        runs first; the ref job then finds its head already served."""
        if job.ref_head_sha is None:
            return False
        return await self.served_head() == ServedBranchHead(job.branch, job.ref_head_sha)


class WatchedBranchPasses:
    """Git-objects passes for tracked branches that are not checked out.

    The indexer is built on first use and kept: building it loads the embedder,
    which a refresh that never leaves the working tree must not pay for.
    """

    __slots__ = ("_build", "_built")

    def __init__(self, build: Callable[[], IndexerWithRebuild]) -> None:
        self._build = build
        self._built: IndexerWithRebuild | None = None

    async def __call__(self, name: str) -> None:
        if self._built is None:
            self._built = await asyncio.to_thread(self._build)
        indexer, rebuild_fulltext_index = self._built
        await run_watched_branch_pass(indexer, name, rebuild_fulltext_index=rebuild_fulltext_index)


__all__ = ("IndexJobRunner", "IndexJobSkipReason", "IndexerWithRebuild", "WatchedBranchPasses")
