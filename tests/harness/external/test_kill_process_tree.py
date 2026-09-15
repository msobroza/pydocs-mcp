"""``_kill_process_group`` — the cancellation cleanup, on both platforms.

The real kill is exercised by ``test_cli_process``; here each platform branch
is driven with a recorder in place of the OS call, because the Windows branch
cannot run on the POSIX CI matrix and the POSIX branch does not exist on
Windows (mypy checks each platform's branch alone — the tag-push CI failed on
``os.killpg`` under Windows before the branch was guarded).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest

from pydocs_mcp.harness.external import harness as harness_module
from pydocs_mcp.harness.external.harness import _kill_process_group


@dataclass(slots=True)
class FakeChildProcess:
    pid: int = 4242


@dataclass(slots=True)
class KillRecorder:
    """Records the one OS-level kill the cleanup issues."""

    calls: list[tuple[Any, ...]] = field(default_factory=list)
    kwargs: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(args)
        self.kwargs.append(kwargs)


def test_posix_kills_the_whole_process_group_with_sigkill(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = KillRecorder()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "getpgid", lambda pid: pid + 1)
    monkeypatch.setattr(os, "killpg", recorder)

    _kill_process_group(FakeChildProcess(pid=4242))  # type: ignore[arg-type]

    assert recorder.calls == [(4243, signal.SIGKILL)]


def test_windows_terminates_the_tree_with_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = KillRecorder()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(harness_module.subprocess, "run", recorder)

    _kill_process_group(FakeChildProcess(pid=4242))  # type: ignore[arg-type]

    assert recorder.calls == [(["taskkill", "/F", "/T", "/PID", "4242"],)]
    assert recorder.kwargs[0]["check"] is False
    assert recorder.kwargs[0]["timeout"] == harness_module._WINDOWS_TASKKILL_TIMEOUT_SECONDS


def test_a_child_already_gone_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _gone(pid: int) -> int:
        raise ProcessLookupError(pid)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "getpgid", _gone)

    assert _kill_process_group(FakeChildProcess()) is None  # type: ignore[arg-type]


def test_a_hung_taskkill_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _hangs(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="taskkill", timeout=kwargs.get("timeout", 0.0))

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(harness_module.subprocess, "run", _hangs)

    assert _kill_process_group(FakeChildProcess()) is None  # type: ignore[arg-type]
