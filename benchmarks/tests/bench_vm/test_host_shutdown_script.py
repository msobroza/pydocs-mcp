"""The host shutdown script, run for real by bash against a stub ``docker``.

``PATH`` holds only the stub plus the base-OS tools the script may use, so a
script that reached for anything else (gcloud, jq, python) fails here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.commit_layout import HOST_SHUTDOWN_SCRIPT, HOST_STARTUP_SCRIPT
from pydocs_eval.bench_vm.supervisor import SupervisorTiming

from ._spec_dicts import REPO_ROOT

SCRIPT = REPO_ROOT / HOST_SHUTDOWN_SCRIPT
_BASH = shutil.which("bash") or "/bin/bash"
# Compute Engine gives a shutdown script about 90 s on a stop or delete, and a
# spot VM 30 s after its preemption notice: the grace must fit the longer one,
# and the supervisor pushes the resume-critical files first for the shorter.
_COMPUTE_ENGINE_SHUTDOWN_WINDOW_S = 90
# The final pushes and the record write, after the child's shutdown stop.
_FINAL_PUSH_ALLOWANCE_S = 45

_STUB = """#!{python}
import json, os, sys
with open(os.environ["STUB_CALLS"], "a") as f:
    f.write(json.dumps({{"tool": "docker", "argv": sys.argv[1:]}}) + "\\n")
if sys.argv[1:3] == ["container", "inspect"]:
    sys.exit(0 if os.environ.get("STUB_CONTAINER_EXISTS") == "1" else 1)
if sys.argv[1:2] == ["stop"]:
    sys.exit(int(os.environ.get("STUB_STOP_EXIT", "0")))
"""


@dataclass
class Shutdown:
    returncode: int
    stderr: str
    calls: list[list[str]]

    def docker(self, verb: str) -> list[list[str]]:
        return [argv for argv in self.calls if argv[0] == verb]


def _shut_down(tmp_path: Path, *, container_exists: bool = True, stop_exit: int = 0) -> Shutdown:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(_STUB.format(python=sys.executable), encoding="utf-8")
    stub.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    env = {
        "PATH": str(bin_dir),
        "STUB_CALLS": str(calls),
        "STUB_CONTAINER_EXISTS": "1" if container_exists else "0",
        "STUB_STOP_EXIT": str(stop_exit),
    }
    completed = subprocess.run(
        [_BASH, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60, check=False
    )
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return Shutdown(completed.returncode, completed.stderr, [json.loads(x)["argv"] for x in lines])


def _startup_container_name() -> str:
    text = (REPO_ROOT / HOST_STARTUP_SCRIPT).read_text(encoding="utf-8")
    found = re.search(r"^readonly CONTAINER_NAME=(\S+)$", text, flags=re.MULTILINE)
    assert found is not None
    return found.group(1)


def test_the_shutdown_script_passes_shellcheck() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed; bench-vm-check runs it in CI")
    completed = subprocess.run(
        [shellcheck, str(SCRIPT)], capture_output=True, text=True, timeout=60, check=False
    )
    assert completed.returncode == 0, completed.stdout


def test_the_script_stops_the_supervisor_container_the_startup_script_started(
    tmp_path: Path,
) -> None:
    shutdown = _shut_down(tmp_path)

    assert shutdown.returncode == 0, shutdown.stderr
    (stop,) = shutdown.docker("stop")
    assert stop[-1] == _startup_container_name()
    assert "--time" in stop


def test_the_grace_leaves_the_supervisor_time_for_the_final_push(tmp_path: Path) -> None:
    shutdown = _shut_down(tmp_path)

    (stop,) = shutdown.docker("stop")
    grace_s = int(stop[stop.index("--time") + 1])
    child_stop_s = SupervisorTiming(sync_interval_s=1, soft_deadline_s=1).shutdown_stop_grace_s
    assert grace_s >= child_stop_s + _FINAL_PUSH_ALLOWANCE_S
    assert grace_s < _COMPUTE_ENGINE_SHUTDOWN_WINDOW_S


def test_no_container_stops_nothing_and_succeeds(tmp_path: Path) -> None:
    shutdown = _shut_down(tmp_path, container_exists=False)

    assert shutdown.returncode == 0, shutdown.stderr
    assert shutdown.docker("stop") == []
    assert json.loads(shutdown.stderr.strip().splitlines()[-1])["event"] == (
        "bench_vm_host_shutdown_skipped"
    )


def test_a_failed_stop_fails_the_script_with_a_json_log_line(tmp_path: Path) -> None:
    shutdown = _shut_down(tmp_path, stop_exit=1)

    assert shutdown.returncode != 0
    assert json.loads(shutdown.stderr.strip().splitlines()[-1])["event"] == "bench_vm_host_failed"


def test_every_log_line_is_one_json_object_with_an_event(tmp_path: Path) -> None:
    shutdown = _shut_down(tmp_path)

    lines = [line for line in shutdown.stderr.splitlines() if line.strip()]
    assert lines
    assert all("event" in json.loads(line) for line in lines)


def test_the_script_is_executable_bash() -> None:
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/bash\n")
    assert os.access(SCRIPT, os.X_OK)
