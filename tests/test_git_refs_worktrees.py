"""read_worktree_checkouts: every worktree's checkout from the plumbing files (#314).

The request-path twin of ``git worktree list`` (spec §6.6, AC-31): a tool call
that names a branch finds its live checkout without spawning git. Unreadable or
unrecognized layouts degrade to ``()``, like every reader in ``git/refs.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pydocs_mcp.git.refs import read_worktree_checkouts
from tests._git_sandbox import (
    NoProcessSpawned,
    commit_text,
    isolate_git_config,
    requires_git,
    run_git,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _main_repo(root: Path, branch: str = "main") -> Path:
    _write(root / ".git" / "HEAD", f"ref: refs/heads/{branch}\n")
    return root / ".git"


def _linked(common: Path, name: str, root: Path, head: str, *, relative: bool = False) -> None:
    admin = common / "worktrees" / name
    root.mkdir(parents=True, exist_ok=True)
    _write(root / ".git", f"gitdir: {admin}\n")
    pointer = Path("..", "..", "..", "..", root.name, ".git") if relative else root / ".git"
    _write(admin / "gitdir", f"{pointer}\n")
    _write(admin / "HEAD", head)
    _write(admin / "commondir", "../..\n")


def test_lists_the_main_worktree_and_each_linked_one_with_its_branch(tmp_path: Path) -> None:
    common = _main_repo(tmp_path / "main")
    _linked(common, "wt", tmp_path / "wt", "ref: refs/heads/feature/x\n")
    _linked(common, "loose", tmp_path / "loose", "a" * 40 + "\n")  # detached
    expected = (
        ((tmp_path / "main").resolve(), "main"),
        ((tmp_path / "loose").resolve(), None),
        ((tmp_path / "wt").resolve(), "feature/x"),
    )
    assert read_worktree_checkouts(tmp_path / "main") == expected
    # The same answer from inside a linked worktree: one repository, one list.
    assert read_worktree_checkouts(tmp_path / "wt") == expected


def test_a_relative_gitdir_pointer_resolves_against_its_admin_directory(tmp_path: Path) -> None:
    common = _main_repo(tmp_path / "main")
    _linked(common, "wt", tmp_path / "wt", "ref: refs/heads/dev\n", relative=True)
    assert ((tmp_path / "wt").resolve(), "dev") in read_worktree_checkouts(tmp_path / "main")


def test_a_worktree_whose_directory_is_gone_is_left_out(tmp_path: Path) -> None:
    common = _main_repo(tmp_path / "main")
    _linked(common, "gone", tmp_path / "gone", "ref: refs/heads/old\n")
    (tmp_path / "gone" / ".git").unlink()
    (tmp_path / "gone").rmdir()
    assert read_worktree_checkouts(tmp_path / "main") == (((tmp_path / "main").resolve(), "main"),)


def test_an_unreadable_admin_entry_costs_only_that_entry(tmp_path: Path) -> None:
    common = _main_repo(tmp_path / "main")
    _write(common / "worktrees" / "stray", "not a directory\n")
    (common / "worktrees" / "broken").mkdir()  # no gitdir pointer, no HEAD
    assert read_worktree_checkouts(tmp_path / "main") == (((tmp_path / "main").resolve(), "main"),)


def test_no_repository_reads_as_no_worktree(tmp_path: Path) -> None:
    assert read_worktree_checkouts(tmp_path) == ()


@pytest.fixture
def sandboxed_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


@requires_git
def test_agrees_with_git_worktree_add_without_spawning_git(
    tmp_path: Path, sandboxed_git: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit_text(root, "a.py", "x = 1\n", "init")
    run_git(root, "branch", "feature/x")
    run_git(root, "worktree", "add", "-q", str(tmp_path / "wt"), "feature/x")
    monkeypatch.setattr(subprocess, "Popen", NoProcessSpawned)
    assert read_worktree_checkouts(root) == (
        (root.resolve(), "main"),
        ((tmp_path / "wt").resolve(), "feature/x"),
    )
