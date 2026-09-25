"""Temporary git repositories for tests that drive a real ``git`` binary.

Every such test builds its repository under ``tmp_path`` through these helpers
and never touches this checkout's own ``.git``: :func:`run_git` drops the
repository-redirecting variables (a pytest run launched from a git hook
inherits ``GIT_DIR``, which would aim ``git init`` / ``git commit`` at the
invoking repository), and :func:`isolate_git_config` points the user and
system config away, so ``core.autocrlf``, ``commit.gpgsign`` or
``grep.patternType`` on the developer's machine cannot change what the adapter
under test sees.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pydocs_mcp.git.env import REPOSITORY_OVERRIDE_VARS

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

_IDENTITY = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x",
}
# Config sources that outrank HOME: each would let the developer's own git
# settings leak into the adapter's child processes.
_CONFIG_OVERRIDE_VARS = (
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
)


def isolate_git_config(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """Give every git child (fixture helper AND adapter under test) an empty config."""
    for name in (*REPOSITORY_OVERRIDE_VARS, *_CONFIG_OVERRIDE_VARS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key, value in _IDENTITY.items():
        monkeypatch.setenv(key, value)


def run_git(root: Path, *args: str) -> str:
    """``git -C root …`` for fixture setup; stripped stdout, raises on failure."""
    inherited = {k: v for k, v in os.environ.items() if k not in REPOSITORY_OVERRIDE_VARS}
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env=inherited | _IDENTITY,
    ).stdout.strip()


def commit_bytes(root: Path, relative: str, content: bytes, message: str) -> str:
    """Write ``content`` verbatim at ``relative``, commit it, return the new HEAD sha."""
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    run_git(root, "add", "--", relative)
    run_git(root, "commit", "-q", "-m", message)
    return run_git(root, "rev-parse", "HEAD")


def commit_text(root: Path, relative: str, text: str, message: str) -> str:
    return commit_bytes(root, relative, text.encode("utf-8"), message)


class NoProcessSpawned(subprocess.Popen):  # type: ignore[type-arg]
    """A ``subprocess.Popen`` stand-in that fails the test on any spawn.

    Patch it in (``monkeypatch.setattr(subprocess, "Popen", NoProcessSpawned)``)
    to pin that a path reads git's plumbing files only: nothing on the request
    path spawns git (AC-31).
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError(f"a process was spawned: {args!r}")


# The git subcommands that talk to a remote (their transport helpers and ssh
# are git's own children): #318 AC 1 asserts none runs while
# ``git.remote.auto_fetch`` is off.
NETWORK_GIT_SUBCOMMANDS = frozenset({"ls-remote", "fetch", "pull", "push", "clone"})


class SpawnRecorder:
    """Records the argv of every process spawned while installed, then spawns it.

    ``install(monkeypatch)`` patches ``subprocess.Popen`` (which
    ``subprocess.run`` builds on) for the rest of the test, in every thread —
    the refresh loop runs git through ``asyncio.to_thread``. The recording is
    the assertion AC 1 of #318 reads: which git commands really ran.
    """

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder, real_popen = self, subprocess.Popen

        class _RecordingPopen(real_popen):  # type: ignore[misc, valid-type]
            def __init__(self, args: object, *rest: object, **kwargs: object) -> None:
                argv = [args] if isinstance(args, str | os.PathLike) else args
                recorder.commands.append(tuple(str(part) for part in argv))  # type: ignore[union-attr]
                super().__init__(args, *rest, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(subprocess, "Popen", _RecordingPopen)

    def git_subcommands(self) -> list[str]:
        """The subcommand of each recorded ``git`` spawn, past ``-C`` / ``-c`` options."""
        return [_git_subcommand(argv) for argv in self.commands if _is_git(argv)]

    def network_commands(self) -> list[tuple[str, ...]]:
        return [
            argv
            for argv in self.commands
            if _is_git(argv) and _git_subcommand(argv) in NETWORK_GIT_SUBCOMMANDS
        ]


def _is_git(argv: tuple[str, ...]) -> bool:
    return bool(argv) and Path(argv[0]).name in ("git", "git.exe")


def _git_subcommand(argv: tuple[str, ...]) -> str:
    rest = list(argv[1:])
    while rest and rest[0] in ("-C", "-c"):
        rest = rest[2:]
    return rest[0] if rest else ""
