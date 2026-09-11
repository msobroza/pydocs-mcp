"""``harness-ask-your-docs`` turns Streamlit's file watcher off by default.

The launcher is an end-user entry point, not a dev-reload loop: with the
[sentence-transformers] extra installed, Streamlit's local_sources_watcher walks every
imported module and touches transformers' lazy attributes, each raising a benign
torchvision ModuleNotFoundError — about 1,400 traceback lines per rerun. Streamlit is
last-flag-wins, so the default must precede the operator's ``-- <passthrough>`` args.

Core deps only: ``cli._require_extra`` is a no-op and ``subprocess.run`` is the named
``FakeStreamlitRun``, so nothing starts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.harness.ask_your_docs import cli

from ._launcher_fakes import FakeStreamlitRun

_WATCHER_FLAG = "--server.fileWatcherType"


def _extra_installed() -> None:
    """The launcher's extra guard, satisfied (no streamlit needed to record the spawn)."""


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> FakeStreamlitRun:
    fake = FakeStreamlitRun()
    monkeypatch.setattr(cli, "_require_extra", _extra_installed)
    monkeypatch.setattr(cli.subprocess, "run", fake)
    return fake


def _watcher_values(cmd: list[str]) -> list[str]:
    """Every value given to the watcher flag, in argv order."""
    return [cmd[i + 1] for i, token in enumerate(cmd) if token == _WATCHER_FLAG]


def test_file_watcher_is_off_by_default(launched: FakeStreamlitRun) -> None:
    assert cli.main([]) == 0
    assert _watcher_values(launched.cmd) == ["none"]
    app_index = launched.cmd.index(str(Path(cli.__file__).with_name("app.py")))
    assert launched.cmd.index(_WATCHER_FLAG) < app_index  # streamlit options precede the app


def test_operator_passthrough_re_enables_the_watcher(launched: FakeStreamlitRun) -> None:
    assert cli.main(["--", _WATCHER_FLAG, "auto"]) == 0
    assert _watcher_values(launched.cmd) == ["none", "auto"]  # the operator's value comes last
