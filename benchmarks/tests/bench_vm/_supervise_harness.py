"""Drive ``python -m pydocs_eval.bench_vm supervise`` in process, against the run a real launch wrote.

A test launches a spec through the launcher (``LaunchHarness``), so the run
record, the spec copy and the VM's metadata are exactly what the launcher
produces; ``boot`` then serves that VM's metadata from the fake metadata
server, and ``supervise`` runs the supervisor with ``_fixture_child.py`` in
place of the image's ``python`` and short, injected intervals.

The fake cloud holds a fake value for every provider key (``FAKE_PROVIDER_KEYS``),
the GPU probe is a ``FakeGpuProbe`` that sees CUDA unless a test says otherwise,
and before/after clones the fixture repository's ``origin``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pydocs_eval.bench_vm.__main__ import main
from pydocs_eval.bench_vm.provider_secrets import secret_id_for
from pydocs_eval.bench_vm.run_record import RunRecord
from pydocs_eval.bench_vm.supervisor import SupervisorPorts, SupervisorTiming
from pydocs_eval.bench_vm.vm_request import ProvisioningModel, VmCreateRequest

from ._fakes import FakeGpuProbe
from ._git_fixture import run_git
from ._launch_harness import LaunchHarness

FIXTURE_CHILD = Path(__file__).with_name("_fixture_child.py")
_PROJECT_NUMBER = "123456789"
SHORT_TIMING = SupervisorTiming(sync_interval_s=0.2, soft_deadline_s=20.0, stop_grace_s=5.0)
# Fake values only: a test that finds one outside the child's environment fails.
FAKE_PROVIDER_KEYS = {
    "ANTHROPIC_API_KEY": "sk-ant-FAKE-5d1c9e0a-must-stay-in-the-child",
    "OPENROUTER_API_KEY": "sk-or-FAKE-a83f22b7-must-stay-in-the-child",
}
_PREFLIGHT_TIMEOUT_S = 20.0


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
    def invocations(self) -> list[dict[str, Any]]:
        """Every start of the fixture child, pre-flights included, in order."""
        path = Path(f"{self.started_path}.runs.jsonl")
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    @property
    def signals(self) -> list[str]:
        path = Path(f"{self.started_path}.signals")
        return path.read_text(encoding="utf-8").split() if path.exists() else []


@dataclass
class SuperviseHarness:
    launch: LaunchHarness
    root: Path
    monkeypatch: pytest.MonkeyPatch
    gpu_probe: FakeGpuProbe = field(default_factory=FakeGpuProbe)

    def __post_init__(self) -> None:
        self.launch.cloud.secrets.update(
            {secret_id_for(key): value for key, value in FAKE_PROVIDER_KEYS.items()}
        )
        # Served with partial-clone filtering on, as GitHub serves the product.
        run_git(self.launch.repo.origin, "config", "uploadpack.allowFilter", "true")

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

    def relaunch(self, capsys: pytest.CaptureFixture[str], stem: str) -> str:
        """Launch the spec ``stem`` again (the next attempt) and serve its new VM's metadata."""
        result = self.launch.launch_path(capsys, self.launch.specs_dir / f"{stem}.yaml")
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
        preflight_timeout_s: float = _PREFLIGHT_TIMEOUT_S,
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
            gpu_probe=self.gpu_probe,
            product_repo_url=self.launch.repo.origin.as_uri(),
            preflight_timeout_s=preflight_timeout_s,
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

    @property
    def product_clone(self) -> Path:
        return self.cache_dir / "product-repo"

    def state_uri(self, run_id: str) -> str:
        return f"gs://{self.launch.names.names.runs_bucket}/{run_id}/state/"
