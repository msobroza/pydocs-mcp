"""The launcher's fixed inputs in tests: the deployment's names, a VM request, a stepping clock."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pydocs_eval.bench_vm.deployment import DeploymentNames
from pydocs_eval.bench_vm.vm_request import (
    ProvisioningModel,
    TerminationAction,
    VmCreateRequest,
    vm_labels,
)


def fake_vm_request(run_id: str, *, attempt: int, zone: str) -> VmCreateRequest:
    """A bench VM create request for ``run_id``'s ``attempt`` in ``zone``, labelled as launched."""
    return VmCreateRequest(
        project="bench-project",
        zone=zone,
        name=f"bench-{run_id}-{attempt}",
        machine_type="n2-standard-8",
        provisioning_model=ProvisioningModel.SPOT,
        max_run_duration_s=6 * 3600,
        termination_action=TerminationAction.DELETE,
        external_ip=False,
        service_account="bench-vm@bench-project.iam.gserviceaccount.com",
        subnetwork="projects/bench-project/regions/europe-west4/subnetworks/bench",
        boot_image="projects/cos-cloud/global/images/cos-stable-1",
        boot_disk_gb=100,
        boot_disk_auto_delete=True,
        gpu=None,
        labels=vm_labels(run_id, attempt),
        metadata={},
    )


def fake_deployment_names(**overrides: object) -> DeploymentNames:
    values: dict[str, object] = {
        "project_id": "bench-project",
        "region": "europe-west4",
        "zones": ("europe-west4-a", "europe-west4-b", "europe-west4-c"),
        "runs_bucket": "bench-runs",
        "cache_bucket": "bench-cache",
        "image_repository": "europe-west4-docker.pkg.dev/bench-project/bench",
        "bench_service_account": "bench-vm@bench-project.iam.gserviceaccount.com",
        "subnetwork": "projects/bench-project/regions/europe-west4/subnetworks/bench",
        "cpu_boot_image": "projects/cos-cloud/global/images/cos-stable-1",
        "gpu_boot_image": "projects/deeplearning-platform-release/global/images/dlvm-1",
        **overrides,
    }
    return DeploymentNames(**values)


@dataclass
class FakeDeploymentNamesSource:
    """Stands in for ``terraform output``: hands out fixed names and counts reads."""

    names: DeploymentNames = field(default_factory=fake_deployment_names)
    reads: int = 0

    def read_deployment_names(self) -> DeploymentNames:
        self.reads += 1
        return self.names


@dataclass
class SteppingClock:
    """A clock that starts at a fixed instant and moves one second per reading."""

    now: datetime = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.now
        self.now = current + timedelta(seconds=1)
        return current
