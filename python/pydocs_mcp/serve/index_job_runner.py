"""The index job queue's one worker (spec §6.8, #317): which pass each job runs.

- ``BRANCH_INDEX`` of the working-tree branch → the working-tree pass (the same
  pass a file save runs; P2 makes it incremental), unless the job came from ref
  events only and the served row already carries the head they saw;
- ``BRANCH_INDEX`` of another tracked local branch → a git-objects pass
  (#310's ``BranchIndexer``);
- ``BRANCH_INDEX`` of a tracked remote-tracking ref (``git.remote.track_refs``)
  → the same pass at ``refs/remotes/<name>``'s sha (spec §6.8b layer 2, #318);
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

from pydocs_mcp.application.extra_branch_passes import (
    BranchRefIndexer,
    run_remote_ref_pass,
    run_watched_branch_pass,
)
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


async def _no_remote_ref_pass(name: str) -> None:
    """The runner of a refresh that tracks no remote ref: never reached."""
    _log_skipped(name, IndexJobSkipReason.UNTRACKED)


@dataclass(frozen=True, slots=True, kw_only=True)
class IndexJobRunner:
    """Runs one queued job; the queue guarantees one at a time."""

    tracking: BranchTracking
    reindex_working_tree: Callable[[], Awaitable[object]]
    index_other_branch: Callable[[str], Awaitable[object]]
    recheck_merge_bases: Callable[[], Awaitable[object]]
    # The served row's name and head (a SQLite read, never git).
    served_head: Callable[[], Awaitable[ServedBranchHead | None]]
    # ``git.remote.track_refs`` (spec §6.8b layer 2, #318): names indexed from
    # their remote-tracking ref, never a local one.
    tracked_remote_refs: frozenset[str] = frozenset()
    index_remote_ref: Callable[[str], Awaitable[object]] = _no_remote_ref_pass

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
        elif job.branch in self.tracked_remote_refs:
            await self.index_remote_ref(job.branch)
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


async def _no_remote_ref_target(name: str) -> str | None:
    """The target of a refresh that tracks no remote ref: never reached."""
    return None


class WatchedBranchPasses:
    """Git-objects passes for tracked branches that are not checked out.

    The indexer is built on first use and kept: building it loads the embedder,
    which a refresh that never leaves the working tree must not pay for — nor
    one whose tracked remote refs are all indexed already (#318 review), so
    ``remote_ref_target`` decides a remote ref's pass before any build.
    """

    __slots__ = ("_build", "_built", "_remote_ref_target")

    def __init__(
        self,
        build: Callable[[], IndexerWithRebuild],
        remote_ref_target: Callable[[str], Awaitable[str | None]] = _no_remote_ref_target,
    ) -> None:
        self._build = build
        self._built: IndexerWithRebuild | None = None
        self._remote_ref_target = remote_ref_target

    async def __call__(self, name: str) -> None:
        indexer, rebuild_fulltext_index = await self._indexer()
        await run_watched_branch_pass(indexer, name, rebuild_fulltext_index=rebuild_fulltext_index)

    async def remote_ref(self, name: str) -> None:
        """A tracked remote-tracking ref's pass (spec §6.8b layer 2, #318)."""
        sha = await self._remote_ref_target(name)
        if sha is None:
            return
        indexer, rebuild_fulltext_index = await self._indexer()
        await run_remote_ref_pass(indexer, name, sha, rebuild_fulltext_index=rebuild_fulltext_index)

    async def _indexer(self) -> IndexerWithRebuild:
        if self._built is None:
            self._built = await asyncio.to_thread(self._build)
        return self._built


__all__ = ("IndexJobRunner", "IndexJobSkipReason", "IndexerWithRebuild", "WatchedBranchPasses")
