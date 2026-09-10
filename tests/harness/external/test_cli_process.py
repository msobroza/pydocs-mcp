"""harness/external/harness — the one seam that actually spawns.

Every other test replaces ``_run_cli_process`` with a fake, so this module is
where the real process path is exercised: a trivial child for the happy path,
and a sleeping child for the timeout, which must kill the whole process GROUP
(the real CLI spawns MCP-server and tool subprocesses of its own, and an
orphaned tree would keep spending).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pydocs_mcp.harness.external.harness import _run_cli_process


async def test_a_real_child_process_returns_its_decoded_stdout(tmp_path: Path) -> None:
    stdout = await _run_cli_process(
        [sys.executable, "-c", "print('hello from the engine')"],
        cwd=tmp_path,
        timeout_seconds=30.0,
    )
    assert stdout.strip() == "hello from the engine"


async def test_the_child_runs_in_the_requested_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    stdout = await _run_cli_process(
        [sys.executable, "-c", "import pathlib; print(pathlib.Path.cwd().name)"],
        cwd=workspace,
        timeout_seconds=30.0,
    )
    assert stdout.strip() == "ws"


async def test_an_overrunning_child_times_out_and_is_killed(tmp_path: Path) -> None:
    with pytest.raises(TimeoutError):
        await _run_cli_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout_seconds=0.2,
        )
