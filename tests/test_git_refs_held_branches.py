"""read_branches_held_by_worktrees: every branch a worktree is working on (#318).

The remote lane's fast-forward (spec §6.8b layer 4) must never move a branch a
worktree holds. ``git worktree list`` reports a worktree in the middle of a
rebase or a bisect as detached, although git finishes the rebase with a
compare-and-swap on that branch; a fast-forward in between strands the rebased
work on a detached HEAD. This reader adds what those in-progress operations
record, from the plumbing files alone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pydocs_mcp.git.refs import read_branches_held_by_worktrees
from tests._git_sandbox import (
    NoProcessSpawned,
    commit_text,
    isolate_git_config,
    requires_git,
    run_git,
)

DETACHED = "a" * 40 + "\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _linked_admin(common: Path, name: str, root: Path, head: str) -> Path:
    admin = common / "worktrees" / name
    root.mkdir(parents=True, exist_ok=True)
    _write(root / ".git", f"gitdir: {admin}\n")
    _write(admin / "gitdir", f"{root / '.git'}\n")
    _write(admin / "HEAD", head)
    _write(admin / "commondir", "../..\n")
    return admin


def test_a_checkout_a_rebase_and_a_bisect_each_hold_their_branch(tmp_path: Path) -> None:
    main = tmp_path / "main"
    common = main / ".git"
    _write(common / "HEAD", "ref: refs/heads/main\n")
    feature = _linked_admin(common, "wt", tmp_path / "wt", "ref: refs/heads/feature/x\n")
    rebasing = _linked_admin(common, "rb", tmp_path / "rb", DETACHED)
    _write(rebasing / "rebase-merge" / "head-name", "refs/heads/topic\n")
    applying = _linked_admin(common, "am", tmp_path / "am", DETACHED)
    _write(applying / "rebase-apply" / "head-name", "refs/heads/fix\n")
    bisecting = _linked_admin(common, "bi", tmp_path / "bi", DETACHED)
    _write(bisecting / "BISECT_START", "hunt\n")
    expected = {
        "main": common.resolve(),
        "feature/x": feature.resolve(),
        "topic": rebasing.resolve(),
        "fix": applying.resolve(),
        "hunt": bisecting.resolve(),
    }
    assert read_branches_held_by_worktrees(common) == expected
    # The same answer from a linked worktree's own gitdir: one repository.
    assert read_branches_held_by_worktrees(feature) == expected


def test_a_detached_worktree_with_nothing_in_progress_holds_nothing(tmp_path: Path) -> None:
    common = tmp_path / "repo" / ".git"
    _write(common / "HEAD", DETACHED)
    _write(common / "rebase-apply" / "head-name", "detached HEAD\n")  # a detached rebase
    assert read_branches_held_by_worktrees(common) == {}


def test_a_bare_repository_head_is_no_checkout(tmp_path: Path) -> None:
    """A bare common dir's HEAD names a branch nobody works on."""
    common = tmp_path / "bare.git"
    _write(common / "HEAD", "ref: refs/heads/main\n")
    admin = _linked_admin(common, "wt", tmp_path / "wt", "ref: refs/heads/dev\n")
    assert read_branches_held_by_worktrees(admin) == {"dev": admin.resolve()}


def test_unreadable_plumbing_holds_nothing_and_never_raises(tmp_path: Path) -> None:
    common = tmp_path / "repo" / ".git"
    _write(common / "HEAD", "ref: refs/heads/main\n")
    (common / "rebase-merge" / "head-name").mkdir(parents=True)  # a directory, not a file
    _write(common / "worktrees" / "stray", "not a directory\n")
    assert read_branches_held_by_worktrees(common) == {"main": common.resolve()}
    assert read_branches_held_by_worktrees(tmp_path / "nowhere") == {}


@pytest.fixture
def sandboxed_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


@requires_git
def test_a_real_interactive_rebase_holds_its_branch_while_git_lists_it_detached(
    tmp_path: Path, sandboxed_git: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit_text(root, "a.py", "x = 1\n", "init")
    commit_text(root, "b.py", "y = 2\n", "second")
    monkeypatch.setenv("GIT_SEQUENCE_EDITOR", "sed -i -e s/^pick/edit/")
    run_git(root, "rebase", "-q", "-i", "HEAD~1")  # stops at the edit, HEAD detached
    assert "detached" in run_git(root, "worktree", "list", "--porcelain").splitlines()
    monkeypatch.setattr(subprocess, "Popen", NoProcessSpawned)
    gitdir = root / ".git"
    assert read_branches_held_by_worktrees(gitdir) == {"main": gitdir.resolve()}
