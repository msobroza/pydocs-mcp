"""resolve_symref: one ``ref:`` indirection on the plumbing path (spec R14).

No subprocess: these readers run on the request path. The contract under test
is "a symref that points at a missing ref resolves to ``None``, never raises",
plus agreement with git itself on a real repository (loose and packed refs).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.git.refs import resolve_symref
from tests._git_sandbox import commit_text, isolate_git_config, requires_git, run_git

_ORIGIN_HEAD = "refs/remotes/origin/HEAD"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _gitdir(tmp_path: Path) -> Path:
    gitdir = tmp_path / ".git"
    _write(gitdir / "HEAD", "ref: refs/heads/main\n")
    return gitdir


def test_symref_dereferences_once_then_resolves(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    _write(gitdir / _ORIGIN_HEAD, "ref: refs/remotes/origin/main\n")
    _write(gitdir / "refs" / "remotes" / "origin" / "main", "a" * 40 + "\n")
    assert resolve_symref(gitdir, _ORIGIN_HEAD) == "a" * 40


def test_unset_symref_is_none_and_a_plain_ref_passes_through(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    assert resolve_symref(gitdir, _ORIGIN_HEAD) is None
    _write(gitdir / "refs" / "heads" / "main", "b" * 40 + "\n")
    assert resolve_symref(gitdir, "refs/heads/main") == "b" * 40
    assert resolve_symref(gitdir, "HEAD") == "b" * 40


def test_packed_symref_target_resolves_through_packed_refs(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    _write(gitdir / _ORIGIN_HEAD, "ref: refs/remotes/origin/main\n")
    _write(
        gitdir / "packed-refs",
        "# pack-refs with: peeled fully-peeled sorted\n" + "c" * 40 + " refs/remotes/origin/main\n",
    )
    assert resolve_symref(gitdir, _ORIGIN_HEAD) == "c" * 40


def test_a_symref_to_a_missing_ref_is_none(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    _write(gitdir / _ORIGIN_HEAD, "ref: refs/remotes/origin/gone\n")
    assert resolve_symref(gitdir, _ORIGIN_HEAD) is None


def test_a_second_indirection_is_not_followed(tmp_path: Path) -> None:
    # One hop is the contract; the literal ``ref: …`` of a chained symref must
    # never be handed back as if it were a sha.
    gitdir = _gitdir(tmp_path)
    _write(gitdir / _ORIGIN_HEAD, "ref: refs/remotes/origin/alias\n")
    _write(gitdir / "refs" / "remotes" / "origin" / "alias", "ref: refs/remotes/origin/main\n")
    _write(gitdir / "refs" / "remotes" / "origin" / "main", "d" * 40 + "\n")
    assert resolve_symref(gitdir, _ORIGIN_HEAD) is None


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda g: (g / _ORIGIN_HEAD).write_bytes(b"ref: \xff\xfe\n"), id="non-utf8"),
        pytest.param(lambda g: (g / _ORIGIN_HEAD).write_text("ref: \n"), id="empty-target"),
        pytest.param(lambda g: (g / "commondir").write_bytes(b"\xff\xfe"), id="corrupt-commondir"),
    ],
)
def test_corrupt_plumbing_degrades_to_none(tmp_path: Path, corrupt) -> None:
    gitdir = _gitdir(tmp_path)
    _write(gitdir / _ORIGIN_HEAD, "ref: refs/remotes/origin/main\n")
    corrupt(gitdir)
    assert resolve_symref(gitdir, _ORIGIN_HEAD) is None


def test_worktree_gitdir_reads_the_symref_from_the_common_dir(tmp_path: Path) -> None:
    common = _gitdir(tmp_path / "main")
    _write(common / _ORIGIN_HEAD, "ref: refs/remotes/origin/main\n")
    _write(common / "refs" / "remotes" / "origin" / "main", "e" * 40 + "\n")
    worktree_gitdir = common / "worktrees" / "wt"
    _write(worktree_gitdir / "HEAD", "ref: refs/heads/feature\n")
    _write(worktree_gitdir / "commondir", "../..\n")
    assert resolve_symref(worktree_gitdir, _ORIGIN_HEAD) == "e" * 40


@requires_git
def test_agrees_with_git_on_a_real_clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")
    origin = tmp_path / "origin"
    origin.mkdir()
    run_git(origin, "init", "-q", "-b", "main")
    tip = commit_text(origin, "a.py", "a = 1\n", "one")
    run_git(tmp_path, "clone", "-q", str(origin), str(tmp_path / "clone"))
    gitdir = tmp_path / "clone" / ".git"
    assert resolve_symref(gitdir, _ORIGIN_HEAD) == tip
    run_git(tmp_path / "clone", "pack-refs", "--all")
    assert resolve_symref(gitdir, _ORIGIN_HEAD) == tip
    run_git(tmp_path / "clone", "symbolic-ref", _ORIGIN_HEAD, "refs/remotes/origin/gone")
    assert resolve_symref(gitdir, _ORIGIN_HEAD) is None
