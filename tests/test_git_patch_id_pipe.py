"""The two-process ``git … | git patch-id --stable`` pipe (spec §6.2).

The pipe runs both processes under ONE timeout, never buffers the intermediate
diff stream in Python, and translates every failure to ``GitCommandError`` at
the boundary with the failing side named. The hang and failure tests stand a
POSIX script in for the binary: it answers the ``patch-id`` side one way and
the producer side another, and uses ``exec`` so killing the process kills the
sleeper itself.
"""

from __future__ import annotations

import shutil
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from tests._git_sandbox import commit_text, isolate_git_config, requires_git, run_git

pytestmark = requires_git

_POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="a POSIX script stands in for git")
# Far above any timeout used here, far below the 30 s a leaked sleeper would take.
_KILLED_PROMPTLY_SECONDS = 5.0


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit_text(root, "a.py", "a = 1\n", "one")
    commit_text(root, "a.py", "a = 2\n", "two")
    return root


def _pipe_binary(tmp_path: Path, *, producer: str, consumer: str) -> str:
    """A ``git`` whose ``patch-id`` side runs ``consumer`` and every other call ``producer``."""
    script = tmp_path / "pipe-git"
    script.write_text(
        "#!/bin/sh\n"
        'for arg in "$@"; do\n'
        f'  if [ "$arg" = "patch-id" ]; then {consumer}; fi\n'
        "done\n"
        f"{producer}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script)


def _real_git() -> str:
    return f'exec "{shutil.which("git")}" "$@"'


@_POSIX_ONLY
def test_a_hung_consumer_is_killed_at_the_timeout(repo: Path, tmp_path: Path) -> None:
    binary = _pipe_binary(tmp_path, producer=_real_git(), consumer="exec sleep 30")
    git = SubprocessGitRepository(project_root=repo, binary=binary, timeout_seconds=0.3)
    started = time.monotonic()
    with pytest.raises(GitCommandError) as info:
        git.patch_id("main~1", "main")
    assert time.monotonic() - started < _KILLED_PROMPTLY_SECONDS
    assert info.value.reason == "timeout after 0.3s"
    assert "|" in info.value.argv  # the whole pipeline is named
    assert "patch-id" in info.value.argv


@_POSIX_ONLY
def test_a_hung_producer_shares_the_one_timeout(repo: Path, tmp_path: Path) -> None:
    # The consumer quits at once; the producer never writes, so no SIGPIPE ends
    # it. Only the shared deadline stops the wait on the producer.
    binary = _pipe_binary(tmp_path, producer="exec sleep 30", consumer="exit 0")
    git = SubprocessGitRepository(project_root=repo, binary=binary, timeout_seconds=0.3)
    started = time.monotonic()
    with pytest.raises(GitCommandError) as info:
        git.patch_id("main~1", "main")
    assert time.monotonic() - started < _KILLED_PROMPTLY_SECONDS
    assert info.value.reason == "timeout after 0.3s"


@_POSIX_ONLY
def test_a_failing_consumer_is_reported_before_the_producer_it_broke(
    repo: Path, tmp_path: Path
) -> None:
    # The producer may die of SIGPIPE once the consumer quits; that is the
    # echo, the consumer's exit is the cause.
    failing = 'echo "fatal: consumer broke" >&2; exit 3'
    binary = _pipe_binary(tmp_path, producer=_real_git(), consumer=failing)
    git = SubprocessGitRepository(project_root=repo, binary=binary)
    with pytest.raises(GitCommandError) as info:
        git.patch_id("main~1", "main")
    assert info.value.reason == "exit 3"
    assert "patch-id" in info.value.argv
    assert "|" not in info.value.argv
    assert "consumer broke" in info.value.stderr_tail


def test_a_failing_producer_is_reported_with_its_stderr(repo: Path) -> None:
    git = SubprocessGitRepository(project_root=repo)
    with pytest.raises(GitCommandError) as info:
        git.patch_ids_per_commit("no-such-ref", "main")
    assert "log" in info.value.argv
    assert "patch-id" not in info.value.argv
    assert info.value.reason == "exit 128"
    assert "no-such-ref" in info.value.stderr_tail


def test_a_missing_binary_is_translated_at_the_boundary(repo: Path, tmp_path: Path) -> None:
    git = SubprocessGitRepository(project_root=repo, binary=str(tmp_path / "no-git"))
    with pytest.raises(GitCommandError) as info:
        git.patch_id("main~1", "main")
    assert info.value.reason == "binary not found"


def test_an_option_like_revision_is_refused_before_git_runs(repo: Path, tmp_path: Path) -> None:
    # ``git diff --output=<file>`` would write a file.
    target = tmp_path / "written"
    git = SubprocessGitRepository(project_root=repo)
    with pytest.raises(GitCommandError, match="option-like"):
        git.patch_id(f"--output={target}", "main")
    with pytest.raises(GitCommandError, match="option-like"):
        git.patch_ids_per_commit("main~1", f"--output={target}")
    assert not target.exists()


def test_the_diff_stream_is_never_buffered_in_python(repo: Path) -> None:
    # A multi-megabyte diff flows producer → consumer through the OS pipe;
    # Python only ever holds patch-id's one-line answer.
    size = 4 * 1024 * 1024
    line = "x" * 63 + "\n"
    base = run_git(repo, "rev-parse", "HEAD")
    tip = commit_text(repo, "big.txt", line * (size // len(line)), "big")
    git = SubprocessGitRepository(project_root=repo)
    tracemalloc.start()
    try:
        patch_id = git.patch_id(base, tip)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert patch_id != ""
    assert peak < size // 8
