"""resolve_git_head — every .git layout from spec §D4, no subprocess."""

from pathlib import Path

from pydocs_mcp.application.freshness import resolve_git_head

SHA_A = "a" * 40
SHA_B = "b" * 40


def _make_repo_dir(root: Path, *, ref: str = "refs/heads/main", sha: str = SHA_A) -> Path:
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(f"ref: {ref}\n")
    (git / ref).write_text(f"{sha}\n")
    return git


def test_regular_repo_loose_ref(tmp_path) -> None:
    _make_repo_dir(tmp_path)
    assert resolve_git_head(tmp_path) == SHA_A


def test_detached_head_raw_sha(tmp_path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text(f"{SHA_B}\n")
    assert resolve_git_head(tmp_path) == SHA_B


def test_packed_refs_when_loose_ref_absent(tmp_path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{SHA_A} refs/heads/other\n"
        f"{SHA_B} refs/heads/main\n"
        f"^{'c' * 40}\n"
    )
    assert resolve_git_head(tmp_path) == SHA_B


def test_worktree_gitfile_with_commondir(tmp_path) -> None:
    # Layout: main repo at main/, worktree at wt/ whose .git is a FILE
    # pointing at main/.git/worktrees/wt, which delegates refs via commondir.
    main = tmp_path / "main"
    main_git = _make_repo_dir(main, sha=SHA_A)
    wt_gitdir = main_git / "worktrees" / "wt"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "HEAD").write_text("ref: refs/heads/main\n")
    (wt_gitdir / "commondir").write_text("../..\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {wt_gitdir}\n")
    assert resolve_git_head(wt) == SHA_A


def test_non_git_tree_returns_none(tmp_path) -> None:
    assert resolve_git_head(tmp_path) is None


def test_corrupt_gitfile_returns_none(tmp_path) -> None:
    (tmp_path / ".git").write_text("not a gitdir pointer\n")
    assert resolve_git_head(tmp_path) is None
