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
