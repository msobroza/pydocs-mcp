"""Drive ``python -m pydocs_eval.bench_vm supervise`` in process, against the run a real launch wrote.

A test launches a spec through the launcher (``LaunchHarness``), so the run
record, the spec copy and the VM's metadata are exactly what the launcher
produces; ``boot`` then serves that VM's metadata from the fake metadata
server, and ``supervise`` runs the supervisor with ``_fixture_child.py`` in
place of the image's ``python`` and short, injected intervals.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from pydocs_eval.bench_vm.__main__ import main
from pydocs_eval.bench_vm.run_record import RunRecord
from pydocs_eval.bench_vm.supervisor import SupervisorPorts, SupervisorTiming
from pydocs_eval.bench_vm.vm_request import ProvisioningModel, VmCreateRequest

from ._launch_harness import LaunchHarness

FIXTURE_CHILD = Path(__file__).with_name("_fixture_child.py")
_PROJECT_NUMBER = "123456789"
SHORT_TIMING = SupervisorTiming(sync_interval_s=0.2, soft_deadline_s=20.0, stop_grace_s=5.0)


def instance_metadata_of(request: VmCreateRequest) -> dict[str, str]:
    """What the metadata server answers on the VM ``request`` created."""
    spot = request.provisioning_model is ProvisioningModel.SPOT
    return {
        **{f"instance/attributes/{key}": value for key, value in request.metadata.items()},
        "instance/name": request.name,
        "instance/zone": f"projects/{_PROJECT_NUMBER}/zones/{request.zone}",
        "instance/machine-type": (
            f"projects/{_PROJECT_NUMBER}/machineTypes/{request.machine_type}"
        ),
        "instance/scheduling/preemptible": "TRUE" if spot else "FALSE",
        "project/project-id": request.project,
    }


@dataclass
class SuperviseResult:
    exit_code: int
    err: str
    run_id: str
    started_path: Path

    @property
    def started(self) -> dict[str, Any]:
        """The fixture child's argv, bench environment and working directory."""
        payload: dict[str, Any] = json.loads(self.started_path.read_text(encoding="utf-8"))
        return payload

    @property
    def signals(self) -> list[str]:
        path = Path(f"{self.started_path}.signals")
        return path.read_text(encoding="utf-8").split() if path.exists() else []


@dataclass
class SuperviseHarness:
    launch: LaunchHarness
    root: Path
    monkeypatch: pytest.MonkeyPatch

    @property
    def state_dir(self) -> Path:
        return self.root / "bench" / "state"

    @property
    def cache_dir(self) -> Path:
        return self.root / "bench" / "cache"

    @property
    def repo_dir(self) -> Path:
        return self.root / "opt" / "pydocs-mcp"

    @property
    def vm(self) -> VmCreateRequest:
        return self.launch.cloud.created[-1]

    def launch_run(
        self, capsys: pytest.CaptureFixture[str], stem: str, spec: dict[str, object]
    ) -> str:
        """Launch ``spec`` and serve its VM's metadata; returns the run id."""
        result = self.launch.launch(capsys, stem, spec)
        assert result.exit_code == 0, result.err
        self.launch.cloud.instance_metadata = instance_metadata_of(self.vm)
        return result.run_id

    def supervise(
        self,
        capsys: pytest.CaptureFixture[str],
        run_id: str,
        *,
        timing: SupervisorTiming = SHORT_TIMING,
        child_python: tuple[str, ...] | None = None,
        **child_env: str,
    ) -> SuperviseResult:
        started = self.root / "child-started.json"
        self.monkeypatch.setenv("FIXTURE_RECORD", str(started))
        for key, value in child_env.items():
            self.monkeypatch.setenv(key, value)
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        ports = SupervisorPorts(
            cloud=self.launch.cloud,
            clock=self.launch.clock,
            child_python=child_python or (sys.executable, str(FIXTURE_CHILD)),
            timing=timing,
        )
        exit_code = main(
            [
                "supervise",
                "--state-dir",
                str(self.state_dir),
                "--cache-dir",
                str(self.cache_dir),
                "--repo-dir",
                str(self.repo_dir),
            ],
            supervisor_ports=ports,
        )
        return SuperviseResult(
            exit_code=exit_code, err=capsys.readouterr().err, run_id=run_id, started_path=started
        )

    def record(self, run_id: str) -> RunRecord:
        return self.launch.record(run_id)

    def state_uri(self, run_id: str) -> str:
        return f"gs://{self.launch.names.names.runs_bucket}/{run_id}/state/"
