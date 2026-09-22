"""P1 port methods over refs and remotes: symrefs, upstreams, ls-remote, fetch, CAS.

Every test builds a temporary repository plus a local bare "origin"
(``tests/_git_sandbox.py``) and is skipped without ``git`` on PATH; nothing
reaches the network. The timeout tests stand a hung script in for the binary.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from tests._git_sandbox import commit_text, isolate_git_config, requires_git, run_git

pytestmark = requires_git


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A clone-like repository: ``main`` pushed to a bare ``origin`` with upstream set."""
    root = tmp_path / "r"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit_text(root, "a.py", "a = 1\n", "one")
    run_git(root, "branch", "feature/x")
    bare = tmp_path / "origin.git"
    run_git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    run_git(root, "remote", "add", "origin", str(bare))
    run_git(root, "push", "-q", "-u", "origin", "main")
    return root


def _second_clone(tmp_path: Path) -> Path:
    """Another developer's clone of ``origin``, for moving the remote under ``repo``."""
    other = tmp_path / "other"
    run_git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    return other


def test_symbolic_ref_reads_the_target_and_is_none_when_unset(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    assert git.symbolic_ref("refs/remotes/origin/HEAD") is None
    assert git.symbolic_ref("refs/heads/main") is None  # a plain ref is not a symref
    run_git(repo, "remote", "set-head", "origin", "-a")
    assert git.symbolic_ref("refs/remotes/origin/HEAD") == "refs/remotes/origin/main"
    assert git.symbolic_ref("HEAD") == "refs/heads/main"


def test_a_dangling_symref_names_its_target_and_resolves_to_none(repo: Path) -> None:
    run_git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone")
    git = SubprocessGitRepository(project_root=repo)
    target = git.symbolic_ref("refs/remotes/origin/HEAD")
    assert target == "refs/remotes/origin/gone"
    assert git.head_sha(target) is None


def test_upstream_of_matches_the_exact_branch_only(repo: Path) -> None:
    run_git(repo, "branch", "topic/one")
    git = SubprocessGitRepository(project_root=repo)
    assert git.upstream_of("main") == "origin/main"
    assert git.upstream_of("feature/x") is None
    # ``for-each-ref refs/heads/topic`` prefix-matches ``topic/one``; the
    # adapter must not report another branch's upstream for a missing name.
    assert git.upstream_of("topic") is None
    assert git.upstream_of("no-such-branch") is None


def test_ahead_behind_counts_both_sides(repo: Path, tmp_path: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    assert git.ahead_behind("main", "origin/main") == (0, 0)
    commit_text(repo, "b.py", "b = 2\n", "local only")
    other = _second_clone(tmp_path)
    commit_text(other, "c.py", "c = 3\n", "remote one")
    commit_text(other, "d.py", "d = 4\n", "remote two")
    run_git(other, "push", "-q", "origin", "main")
    run_git(repo, "fetch", "-q", "origin")
    assert git.ahead_behind("main", "origin/main") == (1, 2)
    # A tag named like the branch must not shadow it: the argument is a local branch.
    run_git(repo, "tag", "main", "HEAD~1")
    assert git.ahead_behind("main", "origin/main") == (1, 2)


def test_ls_remote_heads_lists_the_remote_branches(repo: Path) -> None:
    run_git(repo, "push", "-q", "origin", "feature/x")
    git = SubprocessGitRepository(project_root=repo)
    head = git.head_sha("main")
    assert dict(git.ls_remote_heads("origin")) == {"main": head, "feature/x": head}


def test_fetch_moves_remote_tracking_refs_only_and_prunes_on_request(
    repo: Path, tmp_path: Path
) -> None:
    run_git(repo, "push", "-q", "origin", "feature/x")
    run_git(repo, "fetch", "-q", "origin")
    local_main = run_git(repo, "rev-parse", "main")
    other = _second_clone(tmp_path)
    remote_tip = commit_text(other, "c.py", "c = 3\n", "remote")
    run_git(other, "push", "-q", "origin", "main", ":feature/x")
    git = SubprocessGitRepository(project_root=repo)

    git.fetch("origin")
    assert git.head_sha("refs/remotes/origin/main") == remote_tip
    assert git.head_sha("refs/remotes/origin/feature/x") is not None  # no prune asked
    assert git.head_sha("main") == local_main
    assert not (repo / "c.py").exists()  # the working tree is untouched

    git.fetch("origin", prune=True)
    assert git.head_sha("refs/remotes/origin/feature/x") is None


def test_update_ref_if_unchanged_is_a_compare_and_swap(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    old = git.head_sha("main")
    new = commit_text(repo, "b.py", "b = 2\n", "two")
    run_git(repo, "branch", "side", old)
    assert git.update_ref_if_unchanged("refs/heads/side", new, old, "test ff") is True
    assert git.head_sha("side") == new
    assert "test ff" in run_git(repo, "reflog", "show", "--format=%gs", "side")
    assert git.update_ref_if_unchanged("refs/heads/side", old, old, "stale") is False
    assert git.head_sha("side") == new


def test_update_ref_failure_on_an_unmoved_ref_raises(repo: Path) -> None:
    # The ref still holds ``old``, so the refusal is not "somebody moved it":
    # a missing object (or a held lock) must surface, not read as a lost race.
    git = SubprocessGitRepository(project_root=repo)
    old = git.head_sha("main")
    run_git(repo, "branch", "side", old)
    with pytest.raises(GitCommandError, match="exit 128"):
        git.update_ref_if_unchanged("refs/heads/side", "f" * 40, old, "bogus")
    assert git.head_sha("side") == old


def _push_a_remote_commit(tmp_path: Path) -> str:
    """Move ``origin/main`` from another clone; returns the new remote tip."""
    other = _second_clone(tmp_path)
    tip = commit_text(other, "c.py", "c = 3\n", "remote")
    run_git(other, "push", "-q", "origin", "main")
    return tip


def _install_marker_hook(repo: Path, marker: Path) -> None:
    hook = repo / ".git" / "hooks" / "reference-transaction"
    hook.write_text(f'#!/bin/sh\necho "$1" >> "{marker}"\n', encoding="utf-8")
    hook.chmod(0o755)


@pytest.mark.skipif(sys.platform == "win32", reason="the hook is a POSIX shell script")
def test_the_two_repository_writes_never_run_a_hook(repo: Path, tmp_path: Path) -> None:
    # Spec R8: no git subprocess executes a repository hook; fetch and
    # update-ref both fire ``reference-transaction`` unless hooks are off.
    marker = tmp_path / "hook-ran"
    _install_marker_hook(repo, marker)
    run_git(repo, "update-ref", "refs/heads/probe", "main")
    if not marker.exists():
        pytest.skip("this git predates the reference-transaction hook")
    marker.unlink()
    _push_a_remote_commit(tmp_path)
    git = SubprocessGitRepository(project_root=repo)
    git.fetch("origin", prune=True)
    assert not marker.exists()
    old = git.head_sha("main")
    new = commit_text(repo, "b.py", "b = 2\n", "two")
    marker.unlink()  # the test's own ``git commit`` ran the hook
    assert git.update_ref_if_unchanged("refs/heads/probe", new, old, "ff") is True
    assert not marker.exists()


def test_fetch_never_starts_auto_maintenance(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``git maintenance run --auto`` / ``gc --auto`` can detach past the
    # adapter's timeout and rewrite more than refs/remotes/* and objects.
    _push_a_remote_commit(tmp_path)
    trace = tmp_path / "trace.log"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    SubprocessGitRepository(project_root=repo).fetch("origin")
    commands = trace.read_text(encoding="utf-8")
    assert "fetch" in commands
    assert "maintenance run" not in commands
    assert "gc --auto" not in commands


def _pre_atomic_git(tmp_path: Path, calls: Path) -> str:
    """A git that rejects ``fetch --atomic`` the way git < 2.31 does (usage error, exit 129)."""
    script = tmp_path / "old-git"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        'for arg in "$@"; do\n'
        '  if [ "$arg" = "--atomic" ]; then echo "error: unknown option" >&2; exit 129; fi\n'
        "done\n"
        f'exec "{shutil.which("git")}" "$@"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script)


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX script stands in for an old git")
def test_fetch_falls_back_to_a_plain_fetch_when_git_lacks_atomic(
    repo: Path, tmp_path: Path
) -> None:
    tip = _push_a_remote_commit(tmp_path)
    calls = tmp_path / "calls.log"
    SubprocessGitRepository(project_root=repo, binary=_pre_atomic_git(tmp_path, calls)).fetch(
        "origin", prune=True
    )
    attempts = calls.read_text(encoding="utf-8").splitlines()
    assert len(attempts) == 2
    assert "--atomic" in attempts[0]
    assert "--atomic" not in attempts[1]
    assert "--prune" in attempts[1]
    assert run_git(repo, "rev-parse", "refs/remotes/origin/main") == tip


def _hung_binary(tmp_path: Path) -> str:
    script = tmp_path / "hung-git"
    script.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX script stands in for a hung git")
def test_ls_remote_heads_is_bounded_by_the_network_timeout(tmp_path: Path) -> None:
    git = SubprocessGitRepository(
        project_root=tmp_path,
        binary=_hung_binary(tmp_path),
        timeout_seconds=60.0,
        network_timeout_seconds=0.2,
    )
    with pytest.raises(GitCommandError) as info:
        git.ls_remote_heads("origin")
    assert info.value.reason == "timeout after 0.2s"


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX script stands in for a hung git")
def test_local_reads_and_fetch_keep_the_local_timeout(tmp_path: Path) -> None:
    git = SubprocessGitRepository(
        project_root=tmp_path,
        binary=_hung_binary(tmp_path),
        timeout_seconds=0.2,
        network_timeout_seconds=60.0,
    )
    for call in (lambda: git.ls_tree("main"), lambda: git.fetch("origin")):
        with pytest.raises(GitCommandError) as info:
            call()
        assert info.value.reason == "timeout after 0.2s"
