"""Ref events → index jobs (spec §6.8's event table, #317), and which branches the
refresh follows."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.application.extra_branch_passes import ExtraBranchRequest
from pydocs_mcp.models import NON_GIT_BRANCH_NAME
from pydocs_mcp.retrieval.config.git_models import GitBranchesConfig
from pydocs_mcp.serve.index_jobs import IndexJob, IndexJobKind
from pydocs_mcp.serve.ref_watcher import RefEvent, RefEventKind
from pydocs_mcp.serve.refresh_jobs import BranchTracking, TrackedRefs, events_to_jobs

A, B = "a" * 40, "b" * 40
BRANCH, RECHECK = IndexJobKind.BRANCH_INDEX, IndexJobKind.MERGE_BASE_RECHECK


def _jobs(*events: RefEvent, tracked=frozenset(), working="main") -> list[tuple]:
    jobs = events_to_jobs(events, tracked=tracked, working_tree_branch=working)
    return [(j.kind, j.branch, j.priority) for j in jobs]


def test_events_map_to_jobs_per_the_spec_table() -> None:
    assert _jobs(
        RefEvent(RefEventKind.BRANCH_MOVED, "feature/x", B),
        RefEvent(RefEventKind.BRANCH_MOVED, "untracked", B),
        RefEvent(RefEventKind.HEAD_MOVED, "feature/y", None),
        RefEvent(RefEventKind.BRANCH_DELETED, "gone", None),
        RefEvent(RefEventKind.TAG_MOVED, "v2", A),
        RefEvent(RefEventKind.BASE_TIP_MOVED, "origin/main", A),
        RefEvent(RefEventKind.REMOTE_MOVED, "origin/other", A),
        tracked=frozenset({"feature/x", "main"}),
    ) == [
        (BRANCH, "feature/x", 1),
        (BRANCH, "feature/y", 0),
        (RECHECK, "", 3),
        (IndexJobKind.RETENTION_WINDOW, "", 3),
    ]


def test_a_commit_on_the_working_tree_branch_refreshes_that_branch_only() -> None:
    """Refreshed even when ``track`` names other branches only: the working tree
    is the served index."""
    assert _jobs(RefEvent(RefEventKind.BRANCH_MOVED, "main", B), tracked=frozenset({"x"})) == [
        (BRANCH, "main", 0)
    ]


def test_a_new_branch_checked_out_queues_exactly_one_pass() -> None:
    """``git switch -c new``: the ref appears and HEAD moves — one job."""
    assert _jobs(
        RefEvent(RefEventKind.BRANCH_MOVED, "new", A),
        RefEvent(RefEventKind.HEAD_MOVED, "new", None),
        working="new",
    ) == [(BRANCH, "new", 0)]


def test_a_fetch_that_moves_no_base_tip_queues_nothing() -> None:
    """AC-7: ``git fetch`` alone reindexes nothing (the remote lane is §6.8b's)."""
    assert _jobs(RefEvent(RefEventKind.REMOTE_MOVED, "origin/feature/x", A)) == []


def test_jobs_carry_no_paths() -> None:
    (job,) = events_to_jobs(
        (RefEvent(RefEventKind.HEAD_MOVED, "x", None),), tracked=(), working_tree_branch="x"
    )
    assert job == IndexJob(BRANCH, "x", priority=0)


def test_ref_jobs_carry_the_head_the_watcher_saw() -> None:
    """#317: the runner drops a working-tree job whose head the served row
    already carries — a checkout's file job ran first, or the startup pass
    indexed the head the watch's first snapshot saw (``HEAD_AT_START``)."""
    jobs = events_to_jobs(
        (
            RefEvent(RefEventKind.HEAD_AT_START, "main", A),
            RefEvent(RefEventKind.BRANCH_MOVED, "feature/x", B),
        ),
        tracked=frozenset({"feature/x"}),
        working_tree_branch="main",
    )
    assert jobs == (
        IndexJob(BRANCH, "main", priority=0, ref_head_sha=A),
        IndexJob(BRANCH, "feature/x", priority=1, ref_head_sha=B),
    )


def _repo(tmp_path: Path, head: str) -> Path:
    gitdir = tmp_path / ".git"
    for name, sha in {"main": A, "feature/x": B, "feature/y": B, "wip": A}.items():
        ref = gitdir / "refs" / "heads" / name
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(sha + "\n", encoding="utf-8")
    (gitdir / "HEAD").write_text(head + "\n", encoding="utf-8")
    return gitdir


def test_the_default_tracking_follows_the_checked_out_branch(tmp_path: Path) -> None:
    tracking = BranchTracking(_repo(tmp_path, "ref: refs/heads/wip"), GitBranchesConfig())
    assert tracking.read() == TrackedRefs("wip", frozenset({"wip"}))


def test_this_runs_branch_flags_join_the_tracked_set(tmp_path: Path) -> None:
    """#310: a watch cycle re-runs an explicit ``--branch`` pass only when the
    watcher saw that branch move — so the watcher must follow it."""
    gitdir = _repo(tmp_path, "ref: refs/heads/main")
    named = BranchTracking.for_run(
        gitdir, GitBranchesConfig(), ExtraBranchRequest(names=("feature/x",))
    )
    assert named.read().tracked == {"main", "feature/x"}
    every = BranchTracking.for_run(
        gitdir, GitBranchesConfig(), ExtraBranchRequest(all_branches=True)
    )
    assert every.read().tracked == {"main", "feature/x", "feature/y", "wip"}
    globbed = BranchTracking(gitdir, GitBranchesConfig(track=["feature/*"]))
    assert globbed.read().tracked == {"feature/x", "feature/y"}


def test_a_detached_head_tracks_no_checked_out_branch(tmp_path: Path) -> None:
    tracking = BranchTracking(_repo(tmp_path, A), GitBranchesConfig())
    assert tracking.read() == TrackedRefs(f"detached-{A[:7]}", frozenset())


def test_without_a_repository_the_working_tree_is_the_non_git_row() -> None:
    assert BranchTracking(None, GitBranchesConfig()).read() == TrackedRefs(
        NON_GIT_BRANCH_NAME, frozenset()
    )
