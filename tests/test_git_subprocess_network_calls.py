"""The adapter's two network calls, ``ls-remote`` and ``fetch`` (#318 review).

- A timeout kills git's transport children too. ``subprocess.run`` kills only
  its direct child, so the ``ssh`` a hung ``git ls-remote`` started outlived it —
  and the remote lane retries every interval. Each network call now runs in a
  session of its own whose whole process group is killed.
- Every other call forbids lazy fetches (``GIT_NO_LAZY_FETCH``, honored from
  git 2.44): in a partial clone a blob read would otherwise run ``git fetch``
  behind the port's back — network access with auto-fetch off (AC 1).

Nothing reaches the network: the hung transport is a script standing in for ssh.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from pydocs_mcp.git.env import git_child_env
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from tests._git_sandbox import commit_text, isolate_git_config, requires_git, run_git

pytestmark = requires_git

_PROC = Path("/proc")
_TIMEOUT_SECONDS = 1.0
_DEATH_DEADLINE_SECONDS = 5.0
_LAZY_FETCH_OFF = "GIT_NO_LAZY_FETCH"


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


def _repo(tmp_path: Path, origin: str) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit_text(root, "a.py", "a = 1\n", "one")
    run_git(root, "remote", "add", "origin", origin)
    return root


def _hung_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An ``ssh`` that records its pid, then hangs: what a dead remote looks like."""
    pid_file = tmp_path / "transport.pid"
    script = tmp_path / "hung_ssh.sh"
    script.write_text(f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 30\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("GIT_SSH_COMMAND", str(script))
    return pid_file


def _is_gone(pid: int) -> bool:
    """Exited, or a zombie waiting for its new parent to reap it."""
    try:
        stat = (_PROC / str(pid) / "stat").read_text(encoding="utf-8")
    except OSError:
        return True
    return stat.rsplit(")", 1)[1].split()[0] == "Z"


def _assert_transport_killed(pid_file: Path) -> None:
    assert pid_file.exists(), "the transport never started: the test proves nothing"
    pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + _DEATH_DEADLINE_SECONDS
    while not _is_gone(pid):
        if time.monotonic() > deadline:
            subprocess.run(["kill", "-9", str(pid)], check=False)
            pytest.fail(f"transport process {pid} outlived the timed-out git call")
        time.sleep(0.05)


@pytest.mark.skipif(not _PROC.is_dir(), reason="reads process state from /proc")
def test_a_timed_out_ls_remote_leaves_no_transport_process_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = _hung_transport(tmp_path, monkeypatch)
    root = _repo(tmp_path, "ssh://example.invalid/repo.git")
    git = SubprocessGitRepository(project_root=root, network_timeout_seconds=_TIMEOUT_SECONDS)
    with pytest.raises(GitCommandError, match="timeout after 1s"):
        git.ls_remote_heads("origin")
    _assert_transport_killed(pid_file)


@pytest.mark.skipif(not _PROC.is_dir(), reason="reads process state from /proc")
def test_a_timed_out_fetch_leaves_no_transport_process_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = _hung_transport(tmp_path, monkeypatch)
    root = _repo(tmp_path, "ssh://example.invalid/repo.git")
    git = SubprocessGitRepository(project_root=root, timeout_seconds=_TIMEOUT_SECONDS)
    with pytest.raises(GitCommandError, match="timeout after 1s"):
        git.fetch("origin", prune=True)
    _assert_transport_killed(pid_file)


def test_every_child_forbids_lazy_fetches_by_default() -> None:
    assert git_child_env()[_LAZY_FETCH_OFF] == "1"


def test_only_the_two_network_calls_may_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare = tmp_path / "origin.git"
    run_git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    root = _repo(tmp_path, str(bare))
    run_git(root, "push", "-q", "-u", "origin", "main")
    spawned: list[tuple[str, dict[str, str]]] = []
    real_popen = subprocess.Popen

    class _EnvRecordingPopen(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, args, *rest, **kwargs) -> None:  # type: ignore[no-untyped-def]
            subcommand = next(a for a in args[3:] if not a.startswith("-") and "=" not in a)
            spawned.append((subcommand, dict(kwargs["env"])))
            super().__init__(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", _EnvRecordingPopen)
    git = SubprocessGitRepository(project_root=root)
    git.upstream_of("main")
    git.ls_remote_heads("origin")
    git.fetch("origin", prune=True)
    lazy_fetch_off = {sub: env.get(_LAZY_FETCH_OFF) for sub, env in spawned}
    assert lazy_fetch_off == {"for-each-ref": "1", "ls-remote": None, "fetch": None}
