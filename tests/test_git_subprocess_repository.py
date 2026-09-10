"""SubprocessGitRepository against a real repository (skipped without ``git``)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from pydocs_mcp.models import FileChangeKind

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")


def _git(root: Path, *args: str) -> str:
    # Inherit the environment and override only identity + HOME. A replacement
    # env would also replace PATH, and on POSIX subprocess resolves the program
    # through the PASSED env's PATH — so a hardcoded PATH makes the fixture die
    # with FileNotFoundError (rather than skip) wherever git lives elsewhere,
    # and on Windows it also drops SystemRoot/PATHEXT, which git needs.
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
        "HOME": str(root),
    }
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=env
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_conforms_and_reads_branch_and_head(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    assert isinstance(git, GitRepository)
    assert git.current_branch() == "main"
    head = git.head_sha()
    assert head is not None and len(head) == 40


def test_index_manifest_lists_tracked_files_with_blob_ids(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    manifest = dict(git.index_manifest())
    assert set(manifest) == {"pkg/a.py"}
    assert len(manifest["pkg/a.py"]) == 40


def test_hash_objects_matches_git_for_untracked_file(repo: Path) -> None:
    (repo / "pkg" / "b.py").write_text("x = 1\n", encoding="utf-8")
    git = SubprocessGitRepository(project_root=repo)
    ((path, blob),) = git.hash_objects(["pkg/b.py"])
    assert path == "pkg/b.py"
    assert blob == _git(repo, "hash-object", "pkg/b.py").strip()


def test_working_tree_changes_reports_modified_and_untracked(repo: Path) -> None:
    (repo / "pkg" / "a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    (repo / "pkg" / "b.py").write_text("x = 1\n", encoding="utf-8")
    git = SubprocessGitRepository(project_root=repo)
    changes = dict(git.working_tree_changes())
    assert changes == {"pkg/a.py": FileChangeKind.MODIFIED, "pkg/b.py": FileChangeKind.ADDED}


def test_list_worktrees_includes_the_main_checkout(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    worktrees = git.list_worktrees()
    # git prints the path as it sees it; tmp dirs can be symlinked, so resolve both sides.
    assert any(Path(p).resolve() == repo.resolve() and b == "main" for p, b in worktrees)


def test_inherited_gitdir_does_not_redirect_the_adapter(
    repo: Path, tmp_path_factory, monkeypatch
) -> None:
    """``git -C <root>`` only changes directory — ``GIT_DIR`` still overrides
    repository discovery. An index pass run from a ``post-commit`` hook inherits
    it, and the bundle would be stamped with the OTHER repository's branch."""
    other = tmp_path_factory.mktemp("other")
    _git(other, "init", "-q", "-b", "other-branch")
    (other / "f.py").write_text("x = 1\n", encoding="utf-8")
    _git(other, "add", ".")
    _git(other, "commit", "-q", "-m", "other")
    # Read the oracle BEFORE exporting the override: ``_git`` inherits the
    # environment too, so afterwards it would answer from ``other`` as well.
    head = _git(repo, "rev-parse", "HEAD").strip()
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    git = SubprocessGitRepository(project_root=repo)

    assert git.current_branch() == "main"
    assert git.head_sha() == head
    assert set(dict(git.index_manifest())) == {"pkg/a.py"}


def test_failures_become_git_command_error(tmp_path: Path) -> None:
    git = SubprocessGitRepository(project_root=tmp_path)  # not a repository
    with pytest.raises(GitCommandError) as info:
        git.index_manifest()
    assert "ls-files" in str(info.value)


def test_missing_binary_becomes_git_command_error(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo, binary="/nonexistent/git")
    with pytest.raises(GitCommandError) as info:
        git.head_sha()
    assert "binary not found" in str(info.value)
