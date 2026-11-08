"""Drive ``python -m pydocs_eval.bench_vm launch`` in process against named fakes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.__main__ import main
from pydocs_eval.bench_vm.launcher import LaunchPorts
from pydocs_eval.bench_vm.run_record import (
    RunRecord,
    RunStatus,
    RunTransition,
    parse_run_record,
    run_record_bytes,
)
from pydocs_eval.bench_vm.run_spec_file import load_run_spec
from pydocs_eval.bench_vm.vm_request import ImageFlavour

from ._fakes import FAKE_IMAGE_DIGEST, FakeBenchCloud, FakeDeploymentNamesSource, SteppingClock
from ._git_fixture import FixtureRepo
from ._spec_dicts import write_spec


@dataclass
class LaunchResult:
    exit_code: int
    out: str
    err: str
    run_id: str
    spec_path: Path


@dataclass
class LaunchHarness:
    """One fixture repo, one fake cloud, one fake ``terraform output``; launches specs."""

    repo: FixtureRepo
    specs_dir: Path
    cloud: FakeBenchCloud
    names: FakeDeploymentNamesSource = field(default_factory=FakeDeploymentNamesSource)
    clock: SteppingClock = field(default_factory=SteppingClock)

    def publish_images(self, commit: str) -> None:
        """Make every flavour's tag of ``commit`` present in the fake registry."""
        repository = self.names.names.image_repository
        for flavour in ImageFlavour:
            self.cloud.images[f"{repository}/bench-{flavour}:{commit}"] = FAKE_IMAGE_DIGEST

    def write_spec(self, stem: str, spec: dict[str, object]) -> Path:
        return write_spec(self.specs_dir, stem, spec)

    def launch(
        self, capsys: pytest.CaptureFixture[str], stem: str, spec: dict[str, object]
    ) -> LaunchResult:
        path = self.write_spec(stem, spec)
        return self.launch_path(capsys, path)

    def launch_path(self, capsys: pytest.CaptureFixture[str], path: Path) -> LaunchResult:
        ports = LaunchPorts(cloud=self.cloud, deployment_names=self.names, clock=self.clock)
        exit_code = main(["launch", str(path), "--repo", str(self.repo.path)], launch_ports=ports)
        captured = capsys.readouterr()
        return LaunchResult(
            exit_code=exit_code,
            out=captured.out,
            err=captured.err,
            run_id=load_run_spec(path).run_id,
            spec_path=path,
        )

    def record_uri(self, run_id: str) -> str:
        return f"gs://{self.names.names.runs_bucket}/{run_id}/run.json"

    def record(self, run_id: str) -> RunRecord:
        data = self.cloud.object_data(self.record_uri(run_id))
        assert data is not None, f"no run record for {run_id}"
        return parse_run_record(data, source=self.record_uri(run_id))

    def writes(self) -> list[str]:
        return [str(r.args["uri"]) for r in self.cloud.requests if r.operation == "write_object"]

    def record_generation(self, run_id: str) -> int:
        stored = self.cloud.read_object(self.record_uri(run_id))
        assert stored is not None, f"no run record for {run_id}"
        return stored.generation

    def supervisor_records(self, run_id: str, status: RunStatus, *, vm_gone: bool = True) -> None:
        """Stand in for the supervisor: move the run to ``status``; its VM vanishes by default."""
        record = self.record(run_id)
        last = record.history[-1]
        transition = RunTransition(
            status=status,
            at=self.clock(),
            attempt=record.attempt,
            instance=last.instance,
            zone="europe-west4-a",
            machine=last.machine,
            detail=f"{status} (scripted supervisor)",
        )
        self.cloud.put_object(
            self.record_uri(run_id), run_record_bytes(record.with_transition(transition))
        )
        if vm_gone:
            self.cloud.vanish()

    def forget_requests(self) -> None:
        """Start observing afresh: drop the requests and VMs a setup launch left behind."""
        self.cloud.requests.clear()
        self.cloud.created.clear()
        self.cloud.builds.clear()
