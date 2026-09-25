"""The remote lane on real repositories (spec §6.8b, #318): a project whose
``origin`` is a local bare repository, and a second clone that pushes to it.
The lane runs through the real subprocess adapter; every spawned command is
recorded, so the tests state which git commands ran — no network is involved,
the "remote" is a directory."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from pydocs_mcp.application.upstream_status import (
    CheckoutPlace,
    UpstreamStatus,
    UpstreamStatusBoard,
)
from pydocs_mcp.git.refs import locate_gitdir
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from pydocs_mcp.retrieval.config.git_models import RemoteConfig
from pydocs_mcp.serve.index_jobs import IndexJob, IndexJobQueue
from pydocs_mcp.serve.remote_sync import RemoteSyncScheduler, RemoteSyncState
from tests._git_sandbox import (
    SpawnRecorder,
    commit_text,
    isolate_git_config,
    requires_git,
    run_git,
)
from tests.serve._remote_fakes import logged_events

pytestmark = requires_git

_AUTO_FETCH_AND_FAST_FORWARD = {
    "auto_fetch": {"enabled": True},
    "fast_forward_branches_without_worktree": True,
}


@pytest.fixture(autouse=True)
def _sandboxed_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


async def _no_pass(job: IndexJob) -> None:
    return None


def project_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """``proj`` with ``main`` and ``feature/x`` (checked out), both pushed with
    upstreams to the bare ``remote.git``; and ``other``, a clone that pushes."""
    root = tmp_path / "proj"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit_text(root, "app.py", "def run() -> int:\n    return 1\n", "init")
    bare = tmp_path / "remote.git"
    run_git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    run_git(root, "remote", "add", "origin", str(bare))
    run_git(root, "push", "-q", "-u", "origin", "main")
    run_git(root, "switch", "-q", "-c", "feature/x")
    run_git(root, "push", "-q", "-u", "origin", "feature/x")
    other = tmp_path / "other"
    run_git(tmp_path, "clone", "-q", str(bare), str(other))
    return root, other


def push_to_main(other: Path, count: int = 1) -> str:
    """``count`` new commits on the remote's ``main``; its new tip."""
    run_git(other, "switch", "-q", "main")
    for n in range(count):
        commit_text(other, f"landed_{n}.py", f"def landed_{n}() -> int:\n    return {n}\n", "land")
    run_git(other, "push", "-q", "origin", "main")
    return run_git(other, "rev-parse", "HEAD")


def _scheduler(root: Path, config: RemoteConfig, board: UpstreamStatusBoard | None = None):
    return RemoteSyncScheduler(
        git=SubprocessGitRepository(root, timeout_seconds=30.0, network_timeout_seconds=10.0),
        config=config,
        queue=IndexJobQueue(_no_pass),
        gitdir=locate_gitdir(root),
        tracked=lambda: ("main", "feature/x"),
        board=board or UpstreamStatusBoard(),
    )


def test_the_signal_comes_from_the_remote_tracking_ref_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 2: after a fetch by anyone, ahead/behind is read against the
    remote-tracking ref and the fetch age off FETCH_HEAD — no ls-remote, no fetch."""
    root, other = project_with_remote(tmp_path)
    push_to_main(other, count=2)
    run_git(root, "fetch", "-q", "origin")  # the user's own fetch
    fetched_at = (locate_gitdir(root) / "FETCH_HEAD").stat().st_mtime
    recorder = SpawnRecorder()
    recorder.install(monkeypatch)
    statuses = _scheduler(root, RemoteConfig()).refresh_upstream_status()
    here, nowhere = CheckoutPlace.THIS_WORKTREE, CheckoutPlace.NOWHERE
    assert statuses == (
        UpstreamStatus("main", "origin/main", 0, 2, fetched_at, nowhere),
        UpstreamStatus("feature/x", "origin/feature/x", 0, 0, fetched_at, here),
    )
    assert set(recorder.git_subcommands()) == {"for-each-ref", "rev-list"}
    assert recorder.network_commands() == []


async def test_a_moved_remote_head_is_fetched_then_fast_forwarded_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Layers 3 and 4: ``main`` (checked out nowhere) moves to the fetched tip
    with a reflog entry saying why; the checked-out ``feature/x``, its working
    tree and its index are untouched. A second check with nothing moved runs
    ``ls-remote`` alone."""
    root, other = project_with_remote(tmp_path)
    tip = push_to_main(other)
    scheduler = _scheduler(root, RemoteConfig.model_validate(_AUTO_FETCH_AND_FAST_FORWARD))
    feature_head = run_git(root, "rev-parse", "feature/x")
    await scheduler.check_remote_once()
    assert run_git(root, "rev-parse", "origin/main") == tip == run_git(root, "rev-parse", "main")
    assert run_git(root, "reflog", "-1", "--format=%gs", "refs/heads/main") == (
        "pydocs-mcp: fast-forward to origin/main"
    )
    assert run_git(root, "symbolic-ref", "--short", "HEAD") == "feature/x"
    assert run_git(root, "rev-parse", "feature/x") == feature_head
    assert run_git(root, "status", "--porcelain") == ""
    assert not (root / "landed_0.py").exists()
    assert [s.behind for s in scheduler.statuses] == [0, 0]
    recorder = SpawnRecorder()
    recorder.install(monkeypatch)
    await scheduler.check_remote_once()
    assert recorder.git_subcommands() == ["ls-remote"]


def test_a_branch_being_rebased_is_never_fast_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#318 review, reproduced: ``main`` mid ``rebase -i`` (stopped at an
    ``edit``) is listed detached by ``git worktree list``, and origin/main is a
    fast-forward of it. Moving it would make ``rebase --continue`` fail its
    compare-and-swap and strand the rebased work; layer 4 leaves it alone."""
    root, other = project_with_remote(tmp_path)
    run_git(root, "switch", "-q", "main")
    commit_text(root, "wip.py", "def wip() -> int:\n    return 0\n", "wip")
    run_git(root, "push", "-q", "origin", "main")
    run_git(other, "pull", "-q", "--ff-only")
    push_to_main(other)
    run_git(root, "fetch", "-q", "origin")
    monkeypatch.setenv("GIT_SEQUENCE_EDITOR", "sed -i -e s/^pick/edit/")
    run_git(root, "rebase", "-q", "-i", "HEAD~1")
    before = run_git(root, "rev-parse", "refs/heads/main")
    scheduler = _scheduler(root, RemoteConfig.model_validate(_AUTO_FETCH_AND_FAST_FORWARD))
    assert scheduler.fast_forward_branches_without_worktree() == ()
    assert run_git(root, "rev-parse", "refs/heads/main") == before
    run_git(root, "rebase", "--continue")  # the user's rebase still lands
    assert run_git(root, "symbolic-ref", "--short", "HEAD") == "main"


async def test_an_unreachable_remote_is_logged_once_and_stays_offline(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root, _other = project_with_remote(tmp_path)
    run_git(root, "remote", "set-url", "origin", (tmp_path / "nowhere.git").as_uri())
    scheduler = _scheduler(root, RemoteConfig.model_validate(_AUTO_FETCH_AND_FAST_FORWARD))
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await scheduler.check_remote_once()
        await scheduler.check_remote_once()
    assert len(logged_events(caplog, "remote_sync_offline")) == 1
    assert logged_events(caplog, "remote_sync_online") == []
    assert scheduler.state is RemoteSyncState.OFFLINE
