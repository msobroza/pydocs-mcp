"""The remote lane over fakes (spec §6.8b, #318): layer 1's behind-upstream
signal and who runs it, layer 2's jobs, layer 3's change-detect-then-fetch and
layer 4's fast-forward — on an injected sleep and jitter, never the network and
never a fixed wait. The offline behavior is ``test_remote_sync_offline.py``'s."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import pytest

from pydocs_mcp.application.upstream_status import CheckoutPlace, UpstreamStatusBoard
from pydocs_mcp.serve.index_jobs import REMOTE_PRIORITY, IndexJobKind, IndexJobQueue
from pydocs_mcp.serve.ref_watcher import RefEvent, RefEventKind
from pydocs_mcp.serve.remote_sync import RemoteSyncState, UpstreamStatus
from tests._fakes import FakeGitRepository
from tests.serve._remote_fakes import (
    NETWORK_CALLS,
    REPOSITORY_WRITES,
    A,
    B,
    C,
    RecordingGitRepository,
    RefusingRefGit,
    SleepsThenCancels,
    UnreadableUpstreamGit,
    auto_fetch_config,
    logged_events,
    moving_remote_git,
    no_pass,
    remote_config,
    remote_scheduler,
    run_lane_until_it_cancels,
    until,
)

BRANCH = IndexJobKind.BRANCH_INDEX
_FF_MESSAGE = "pydocs-mcp: fast-forward to origin/main"
_REMOTE_MOVED = RefEvent(RefEventKind.REMOTE_MOVED, "origin/feature/x", B)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── Layer 1: the behind-upstream signal ─────────────────────────────────────


def _upstream_git() -> FakeGitRepository:
    return FakeGitRepository(
        upstreams={"main": "origin/main", "feature/x": "origin/feature/x"},
        counts={("main", "origin/main"): (0, 3), ("feature/x", "origin/feature/x"): (2, 0)},
    )


def test_layer_one_reads_the_remote_tracking_ref_alone_and_publishes_it(tmp_path: Path) -> None:
    """AC 2: ahead/behind against ``@{upstream}`` plus the FETCH_HEAD mtime —
    no ls-remote, no fetch — kept for the request path on the board."""
    (tmp_path / "FETCH_HEAD").write_text("", encoding="utf-8")
    os.utime(tmp_path / "FETCH_HEAD", (5000.0, 5000.0))
    git, board = RecordingGitRepository(_upstream_git()), UpstreamStatusBoard()
    tracked = ("main", "feature/x", "wip")  # wip has no upstream: no status
    scheduler = remote_scheduler(git, remote_config(), tmp_path, tracked=tracked, board=board)
    statuses = scheduler.refresh_upstream_status()
    assert statuses == (
        UpstreamStatus("main", "origin/main", 0, 3, 5000.0),
        UpstreamStatus("feature/x", "origin/feature/x", 2, 0, 5000.0),
    )
    assert board.latest() == statuses == scheduler.statuses
    assert set(git.calls) == {"upstream_of", "ahead_behind"}


def test_layer_one_says_where_each_branch_is_checked_out(tmp_path: Path) -> None:
    """#318 review: the hint's sync command depends on it — read from the
    plumbing (a linked worktree holds ``release``), never from a process."""
    gitdir = tmp_path / "repo" / ".git"
    _write(gitdir / "HEAD", "ref: refs/heads/feature/x\n")
    linked = gitdir / "worktrees" / "rel"
    _write(tmp_path / "rel" / ".git", f"gitdir: {linked}\n")
    _write(linked / "gitdir", f"{tmp_path / 'rel' / '.git'}\n")
    _write(linked / "HEAD", "ref: refs/heads/release\n")
    git = _upstream_git()
    git.upstreams["release"] = "origin/release"
    tracked = ("main", "feature/x", "release")
    statuses = remote_scheduler(
        git, remote_config(), gitdir, tracked=tracked
    ).refresh_upstream_status()
    assert {s.branch: s.checked_out for s in statuses} == {
        "main": CheckoutPlace.NOWHERE,
        "feature/x": CheckoutPlace.THIS_WORKTREE,
        "release": CheckoutPlace.OTHER_WORKTREE,
    }


async def test_layer_one_is_off_with_the_behind_hint(tmp_path: Path) -> None:
    git, board = RecordingGitRepository(_upstream_git()), UpstreamStatusBoard()
    scheduler = remote_scheduler(git, remote_config(behind_hint=False), tmp_path, board=board)
    assert scheduler.refresh_upstream_status() == ()
    await scheduler.on_ref_events((_REMOTE_MOVED,))
    assert (git.calls, board.latest(), scheduler.upstream_refresh_pending) == ([], (), False)


def test_a_branch_whose_upstream_cannot_be_read_is_left_out(tmp_path: Path) -> None:
    git = UnreadableUpstreamGit(
        upstreams={"main": "origin/main", "feature/x": "origin/gone"},
        counts={("main", "origin/main"): (0, 1)},
        unreadable="feature/x",
    )
    statuses = remote_scheduler(git, remote_config(), tmp_path).refresh_upstream_status()
    assert statuses == (UpstreamStatus("main", "origin/main", 0, 1, None),)


async def test_the_watcher_hands_a_ref_move_over_and_waits_on_no_git(tmp_path: Path) -> None:
    """#318 review (spec §6.8b "local first"): the ref watcher's hand-off only
    flags the move; the lane's own task runs layer 1, once for every move
    reported meanwhile, so the watcher takes its next snapshot at once."""
    git, board = RecordingGitRepository(_upstream_git()), UpstreamStatusBoard()
    scheduler = remote_scheduler(git, remote_config(), tmp_path, board=board)
    await scheduler.on_ref_events((RefEvent(RefEventKind.TAG_MOVED, "v1", A),))
    assert not scheduler.upstream_refresh_pending
    await scheduler.on_ref_events((_REMOTE_MOVED,))
    await scheduler.on_ref_events((RefEvent(RefEventKind.BRANCH_MOVED, "main", C),))
    assert scheduler.upstream_refresh_pending and git.calls == [] and board.latest() == ()
    await scheduler.refresh_upstream_status_when_moved()
    assert [s.branch for s in board.latest()] == ["main", "feature/x"]
    assert not scheduler.upstream_refresh_pending and git.calls.count("upstream_of") == 2


async def test_with_auto_fetch_off_the_lane_follows_ref_moves_and_spawns_no_network(
    tmp_path: Path,
) -> None:
    """AC 1 (O14): the default lane is layer 1 at start, then a layer-1 refresh
    per reported move — no ls-remote, no fetch, no repository write, no wait."""
    inner = _upstream_git()
    git, board, sleep = RecordingGitRepository(inner), UpstreamStatusBoard(), SleepsThenCancels(1)
    scheduler = remote_scheduler(git, remote_config(), tmp_path, board=board, sleep=sleep)
    lane = asyncio.create_task(scheduler.run_until_cancelled())
    await until(lambda: [s.behind for s in board.latest()] == [3, 0])
    inner.counts[("main", "origin/main")] = (0, 5)  # someone else's fetch
    await scheduler.on_ref_events((_REMOTE_MOVED,))
    await until(lambda: [s.behind for s in board.latest()] == [5, 0])
    lane.cancel()
    with pytest.raises(asyncio.CancelledError):
        await lane
    assert not (NETWORK_CALLS | REPOSITORY_WRITES) & set(git.calls)
    assert sleep.durations == []  # no interval and no backoff: nothing is scheduled


async def test_a_failed_refresh_is_logged_and_the_lane_keeps_following(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    broken: list[bool] = []

    def tracked() -> tuple[str, ...]:
        if broken:
            raise RuntimeError("an unreadable repository layout")
        return ("main", "feature/x")

    board = UpstreamStatusBoard()
    scheduler = remote_scheduler(
        _upstream_git(), remote_config(), tmp_path, tracked=tracked, board=board
    )
    lane = asyncio.create_task(scheduler.run_until_cancelled())
    await until(lambda: len(board.latest()) == 2)
    broken.append(True)
    with caplog.at_level(logging.ERROR, logger="pydocs-mcp"):
        await scheduler.on_ref_events((_REMOTE_MOVED,))
        await until(lambda: bool(logged_events(caplog, "upstream_status_refresh_failed")))
    broken.clear()
    board.publish(())
    await scheduler.on_ref_events((_REMOTE_MOVED,))
    await until(lambda: len(board.latest()) == 2)
    lane.cancel()
    with pytest.raises(asyncio.CancelledError):
        await lane


async def test_tracked_remote_refs_are_asked_for_once_at_start(tmp_path: Path) -> None:
    """Layer 2 without auto-fetch: a remote-tracking ref in ``track_refs`` is
    indexed from what is already fetched, at the remote jobs' priority."""
    queue = IndexJobQueue(no_pass)
    config = remote_config(track_refs=["origin/main"])
    await remote_scheduler(_upstream_git(), config, tmp_path, queue=queue).start_from_last_fetch()
    assert [(j.kind, j.branch, j.priority) for j in queue.snapshot()] == [
        (BRANCH, "origin/main", REMOTE_PRIORITY)
    ]


# ── Layers 3, 2 and 4: change-detect, fetch, then jobs and fast-forwards ───


async def test_fetch_runs_only_when_a_remote_head_moved_then_fast_forwards(
    tmp_path: Path,
) -> None:
    git = moving_remote_git(worktrees=((str(tmp_path), "feature/x"),))
    queue, sleep = IndexJobQueue(no_pass), SleepsThenCancels(3)
    config = auto_fetch_config(track_refs=["origin/main"])
    scheduler = remote_scheduler(git, config, tmp_path, queue=queue, sleep=sleep)
    await run_lane_until_it_cancels(scheduler)
    assert git.fetch_calls == [("origin", True)]  # the second and third checks saw no move
    assert git.updated_refs == [("refs/heads/main", B, A, _FF_MESSAGE)]
    assert git.refs["refs/heads/feature/x"] == A  # checked out in a worktree: never touched
    assert [(j.kind, j.branch, j.priority) for j in queue.snapshot()] == [
        (BRANCH, "origin/main", REMOTE_PRIORITY)
    ]
    assert sleep.durations == [5.0, 5.0, 5.0] and scheduler.state is RemoteSyncState.ONLINE


async def test_the_first_check_compares_with_the_remote_tracking_refs_on_disk(
    tmp_path: Path,
) -> None:
    """A restart re-fetches nothing already fetched: the first ls-remote is
    compared with ``refs/remotes/<remote>/``, not with an empty memory."""
    remotes = tmp_path / "refs" / "remotes" / "origin"
    _write(remotes / "main", B + "\n")
    _write(remotes / "feature" / "x", A + "\n")
    _write(remotes / "HEAD", "ref: refs/remotes/origin/main\n")
    git = RecordingGitRepository(moving_remote_git())
    await remote_scheduler(git, auto_fetch_config(), tmp_path).check_remote_once()
    assert "ls_remote_heads" in git.calls and "fetch" not in git.calls


async def test_a_fast_forward_refused_on_one_branch_leaves_the_others_and_stays_online(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """#306: a held lock on ONE ref raises; that branch is logged and skipped,
    the next still moves, and a fetch that succeeded never reads as offline."""
    git = moving_remote_git(RefusingRefGit, refused="refs/heads/feature/x")
    git.refs["origin/feature/x"] = C
    git.upstreams["release"] = "origin/release"
    git.refs["refs/heads/release"] = A
    git.refs["origin/release"] = C
    git.ancestry |= {(A, C)}
    tracked = ("feature/x", "release")
    scheduler = remote_scheduler(git, auto_fetch_config(), tmp_path, tracked=tracked)
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await scheduler.check_remote_once()
    assert git.updated_refs == [
        ("refs/heads/release", C, A, "pydocs-mcp: fast-forward to origin/release")
    ]
    assert [e["branch"] for e in logged_events(caplog, "remote_sync_fast_forward_failed")] == [
        "feature/x"
    ]
    assert scheduler.state is RemoteSyncState.ONLINE
    assert not logged_events(caplog, "remote_sync_offline")


async def test_a_branch_a_worktree_is_rebasing_is_never_fast_forwarded(tmp_path: Path) -> None:
    """#318 review: ``git worktree list`` reports a worktree mid-rebase as
    detached, and git ends the rebase with a compare-and-swap on the branch —
    moving it meanwhile strands the rebased work. The plumbing says who holds it."""
    gitdir = tmp_path / "repo" / ".git"
    _write(gitdir / "HEAD", A + "\n")  # detached by the rebase
    _write(gitdir / "rebase-merge" / "head-name", "refs/heads/main\n")
    git = moving_remote_git(worktrees=((str(tmp_path / "repo"), None),))
    await remote_scheduler(git, auto_fetch_config(), gitdir, tracked=("main",)).check_remote_once()
    assert git.fetch_calls == [("origin", True)] and git.updated_refs == []


async def test_a_diverged_branch_is_logged_and_left_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    git = moving_remote_git()
    git.ancestry = set()  # main has commits of its own: no fast-forward exists
    scheduler = remote_scheduler(git, auto_fetch_config(), tmp_path, tracked=("main",))
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await scheduler.check_remote_once()
    assert git.updated_refs == []
    assert logged_events(caplog, "remote_sync_diverged") == [
        {"event": "remote_sync_diverged", "branch": "main", "upstream": "origin/main"}
    ]


async def test_fast_forward_is_off_by_default(tmp_path: Path) -> None:
    git = moving_remote_git()
    config = remote_config(auto_fetch={"enabled": True, "interval_seconds": 5})
    await remote_scheduler(git, config, tmp_path, tracked=("main",)).check_remote_once()
    assert git.fetch_calls == [("origin", True)] and git.updated_refs == []


async def test_only_tracked_remote_refs_whose_head_moved_are_queued(tmp_path: Path) -> None:
    remotes = tmp_path / "refs" / "remotes" / "origin"
    _write(remotes / "main", A + "\n")  # origin/main moves A -> B
    _write(remotes / "stable", C + "\n")
    git = FakeGitRepository(remote_heads={"origin": (("main", B), ("stable", C))})
    queue = IndexJobQueue(no_pass)
    config = remote_config(
        auto_fetch={"enabled": True}, track_refs=["origin/main", "origin/stable"]
    )
    await remote_scheduler(git, config, tmp_path, queue=queue).check_remote_once()
    assert [j.branch for j in queue.snapshot()] == ["origin/main"]
