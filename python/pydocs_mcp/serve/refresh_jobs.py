"""Ref events → index jobs (spec §6.8's event table, #317), and which branches the
refresh follows.

| Event | Job |
|---|---|
| ``HEAD_MOVED`` (a checkout) | ``BRANCH_INDEX`` of the new working-tree branch, priority 0 |
| ``HEAD_AT_START`` (the watch's first snapshot) | the same; dropped when already served |
| ``BRANCH_MOVED`` of the working-tree branch | ``BRANCH_INDEX`` of it, priority 0 |
| ``BRANCH_MOVED`` of another tracked branch | ``BRANCH_INDEX`` of it, priority 1 |
| ``BRANCH_DELETED`` / ``BASE_TIP_MOVED`` | ``MERGE_BASE_RECHECK`` |
| ``TAG_MOVED`` | ``RETENTION_WINDOW`` |
| ``REMOTE_MOVED`` / an untracked branch | nothing — a fetch alone reindexes nothing (AC-7) |

Every ``BRANCH_INDEX`` here carries the sha the watcher saw (``ref_head_sha``),
so the runner can drop it when the served row already carries it (#317).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.application.branch_policy import select_tracked_branches
from pydocs_mcp.application.extra_branch_passes import ExtraBranchRequest
from pydocs_mcp.git.refs import HEADS_PREFIX, list_refs, local_branch_of_head, read_head
from pydocs_mcp.models import NON_GIT_BRANCH_NAME
from pydocs_mcp.retrieval.config.git_models import ALL_LOCAL_TRACK_ENTRY, GitBranchesConfig
from pydocs_mcp.serve.index_jobs import (
    LOCAL_BRANCH_PRIORITY,
    MAINTENANCE_PRIORITY,
    WORKING_TREE_PRIORITY,
    IndexJob,
    IndexJobKind,
    JobKey,
)
from pydocs_mcp.serve.ref_watcher import RefEvent, RefEventKind, head_branch_name

_RECHECK_JOB = IndexJob(IndexJobKind.MERGE_BASE_RECHECK, priority=MAINTENANCE_PRIORITY)
_MAINTENANCE_JOBS = {
    RefEventKind.BRANCH_DELETED: _RECHECK_JOB,
    RefEventKind.BASE_TIP_MOVED: _RECHECK_JOB,
    RefEventKind.TAG_MOVED: IndexJob(IndexJobKind.RETENTION_WINDOW, priority=MAINTENANCE_PRIORITY),
}
# A checkout indexes the new branch with no command (spec §6.8); the start-up
# report catches the checkout made while the startup pass ran (#317).
_HEAD_EVENTS = frozenset({RefEventKind.HEAD_MOVED, RefEventKind.HEAD_AT_START})


def events_to_jobs(
    events: Iterable[RefEvent], *, tracked: Collection[str], working_tree_branch: str
) -> tuple[IndexJob, ...]:
    """The jobs one diff warrants, one per key, in first-seen order."""
    jobs = (_job_for(event, tracked, working_tree_branch) for event in events)
    merged: dict[JobKey, IndexJob] = {}
    for job in jobs:
        if job is not None:
            merged[job.key] = merged[job.key].merged_with(job) if job.key in merged else job
    return tuple(merged.values())


def _job_for(
    event: RefEvent, tracked: Collection[str], working_tree_branch: str
) -> IndexJob | None:
    if event.kind in _HEAD_EVENTS:
        return _ref_job(event, WORKING_TREE_PRIORITY)
    if event.kind is RefEventKind.BRANCH_MOVED:
        return _branch_job(event, tracked, working_tree_branch)
    return _MAINTENANCE_JOBS.get(event.kind)


def _branch_job(
    event: RefEvent, tracked: Collection[str], working_tree_branch: str
) -> IndexJob | None:
    # The working tree is the served index: refreshed whatever ``track`` says.
    if event.name == working_tree_branch:
        return _ref_job(event, WORKING_TREE_PRIORITY)
    if event.name in tracked:
        return _ref_job(event, LOCAL_BRANCH_PRIORITY)
    return None


def _ref_job(event: RefEvent, priority: int) -> IndexJob:
    return IndexJob(
        IndexJobKind.BRANCH_INDEX, event.name, priority=priority, ref_head_sha=event.sha
    )


@dataclass(frozen=True, slots=True)
class TrackedRefs:
    """The working-tree branch's name and the tracked local branches, read now."""

    working_tree_branch: str
    tracked: frozenset[str]


@dataclass(frozen=True, slots=True)
class BranchTracking:
    """Which branches the refresh follows (spec §6.9), read through the plumbing
    files only. ``gitdir`` is ``None`` without a repository or with git off: the
    working tree is then the non-git row, as the working-tree pass names it."""

    gitdir: Path | None
    branches: GitBranchesConfig

    @classmethod
    def for_run(
        cls, gitdir: Path | None, branches: GitBranchesConfig, extra: ExtraBranchRequest
    ) -> BranchTracking:
        """``git.branches.track`` plus this run's ``--branch`` / ``--all-branches``.

        #310: a watch-cycle reindex never repeats those passes; the watcher
        re-runs one only when it sees that branch move, so it must follow it.
        """
        entries = [*extra.names, *([ALL_LOCAL_TRACK_ENTRY] if extra.all_branches else [])]
        return cls(gitdir, branches.model_copy(update={"track": [*branches.track, *entries]}))

    def working_tree_branch(self) -> str:
        return head_branch_name(read_head(self.gitdir)) if self.gitdir else NON_GIT_BRANCH_NAME

    def read(self) -> TrackedRefs:
        """Blocking plumbing reads (no subprocess); call it off the event loop."""
        if self.gitdir is None:
            return TrackedRefs(NON_GIT_BRANCH_NAME, frozenset())
        head = read_head(self.gitdir)
        local = [ref.removeprefix(HEADS_PREFIX) for ref in list_refs(self.gitdir, HEADS_PREFIX)]
        chosen = select_tracked_branches(self.branches, local, local_branch_of_head(head))
        return TrackedRefs(head_branch_name(head), frozenset(chosen))


__all__ = ("BranchTracking", "TrackedRefs", "events_to_jobs")
