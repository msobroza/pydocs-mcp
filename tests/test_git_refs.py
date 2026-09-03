"""Plumbing-file readers (spec §6.2): no subprocess, worktree-aware, degrade to None."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.git.refs import locate_gitdir, resolve_git_branch, resolve_git_head

_SHA = "8783c8c1111111111111111111111111111111aa"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo_with_loose_ref(root: Path, branch: str = "feature/x") -> None:
    _write(root / ".git" / "HEAD", f"ref: refs/heads/{branch}\n")
    _write(root / ".git" / "refs" / "heads" / branch, _SHA + "\n")


def test_resolve_git_branch_reads_symbolic_head(tmp_path: Path) -> None:
    _repo_with_loose_ref(tmp_path)
    assert resolve_git_branch(tmp_path) == "feature/x"
    assert resolve_git_head(tmp_path) == _SHA


def test_resolve_git_branch_is_none_when_detached(tmp_path: Path) -> None:
    _write(tmp_path / ".git" / "HEAD", _SHA + "\n")
    assert resolve_git_branch(tmp_path) is None
    assert resolve_git_head(tmp_path) == _SHA


def test_resolve_git_branch_is_none_without_repository(tmp_path: Path) -> None:
    assert resolve_git_branch(tmp_path) is None
    assert locate_gitdir(tmp_path) is None


def test_packed_ref_resolves_head(tmp_path: Path) -> None:
    _write(tmp_path / ".git" / "HEAD", "ref: refs/heads/main\n")
    _write(tmp_path / ".git" / "packed-refs", f"# pack-refs\n{_SHA} refs/heads/main\n")
    assert resolve_git_head(tmp_path) == _SHA
    assert resolve_git_branch(tmp_path) == "main"


def test_worktree_gitfile_delegates_refs_to_commondir(tmp_path: Path) -> None:
    main = tmp_path / "main"
    _repo_with_loose_ref(main, "main")
    wt = tmp_path / "wt"
    wt_gitdir = main / ".git" / "worktrees" / "wt"
    _write(wt / ".git", f"gitdir: {wt_gitdir}\n")
    _write(wt_gitdir / "HEAD", "ref: refs/heads/feature/y\n")
    _write(wt_gitdir / "commondir", "../..\n")
    _write(main / ".git" / "refs" / "heads" / "feature" / "y", _SHA + "\n")
    assert resolve_git_branch(wt) == "feature/y"
    assert resolve_git_head(wt) == _SHA


def test_freshness_module_still_exports_resolve_git_head() -> None:
    from pydocs_mcp.application.freshness import resolve_git_head as via_freshness

    assert via_freshness is resolve_git_head
