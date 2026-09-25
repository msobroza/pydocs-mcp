"""The ref-listing plumbing readers the ref watcher snapshots through (spec §6.8,
#317): loose refs plus ``packed-refs``, loose winning, never a subprocess, never
a raise."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.git.refs import list_refs, local_branch_of_head, read_head

A, B, C = "a" * 40, "b" * 40, "c" * 40


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _gitdir(tmp_path: Path) -> Path:
    gitdir = tmp_path / ".git"
    _write(gitdir / "HEAD", "ref: refs/heads/main\n")
    _write(gitdir / "refs" / "heads" / "main", A + "\n")
    _write(gitdir / "refs" / "heads" / "feature" / "x", B + "\n")
    _write(
        gitdir / "packed-refs",
        f"# pack-refs with: peeled fully-peeled sorted\n{C} refs/remotes/origin/main\n"
        f"{C} refs/tags/v1\n^{A}\n",
    )
    return gitdir


def test_loose_and_packed_refs_merge_under_the_prefix_with_loose_winning(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    assert list_refs(gitdir, "refs/heads/") == {"refs/heads/main": A, "refs/heads/feature/x": B}
    assert list_refs(gitdir, "refs/remotes/origin/") == {"refs/remotes/origin/main": C}
    # A peeled-tag line ('^') names no ref.
    assert list_refs(gitdir, "refs/tags/") == {"refs/tags/v1": C}
    _write(gitdir / "refs" / "remotes" / "origin" / "main", A + "\n")
    assert list_refs(gitdir, "refs/remotes/origin/") == {"refs/remotes/origin/main": A}


def test_a_lock_file_mid_update_is_not_a_ref(tmp_path: Path) -> None:
    """git writes ``<ref>.lock`` then renames it over the ref: the lock is not a branch."""
    gitdir = _gitdir(tmp_path)
    _write(gitdir / "refs" / "heads" / "main.lock", B + "\n")
    assert list_refs(gitdir, "refs/heads/") == {"refs/heads/main": A, "refs/heads/feature/x": B}


def test_a_worktree_lists_the_refs_of_its_common_dir(tmp_path: Path) -> None:
    common = _gitdir(tmp_path / "main")
    worktree_gitdir = common / "worktrees" / "wt"
    _write(worktree_gitdir / "HEAD", "ref: refs/heads/feature/x\n")
    _write(worktree_gitdir / "commondir", "../..\n")
    assert list_refs(worktree_gitdir, "refs/heads/") == list_refs(common, "refs/heads/")
    assert read_head(worktree_gitdir) == "ref: refs/heads/feature/x"


def test_an_unreadable_layout_degrades_to_nothing(tmp_path: Path) -> None:
    assert list_refs(tmp_path / "missing", "refs/heads/") == {}
    assert read_head(tmp_path / "missing") == ""
    gitdir = _gitdir(tmp_path)
    (gitdir / "packed-refs").write_bytes(b"\xff\xfe not utf-8")
    assert list_refs(gitdir, "refs/remotes/origin/") == {}
    assert list_refs(gitdir, "refs/heads/")["refs/heads/main"] == A


def test_the_head_line_names_a_local_branch_only_under_refs_heads() -> None:
    assert local_branch_of_head("ref: refs/heads/feature/x") == "feature/x"
    assert local_branch_of_head(A) is None
    assert local_branch_of_head("ref: refs/remotes/origin/main") is None
