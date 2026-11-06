"""What the supervisor learns from the metadata server: which run this VM is for, and where it runs.

The launcher sets the run's keys as instance metadata (``vm_request``'s
``METADATA_*``); Compute Engine answers the rest (name, zone, machine type,
provisioning). Every read is a metadata-server request through
``BenchCloud.read_instance_metadata``, with its timeout and bounded retry.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_eval.bench_vm.bench_cloud import BenchCloud
from pydocs_eval.bench_vm.supervisor_refusal import SupervisorRefused
from pydocs_eval.bench_vm.vm_request import (
    METADATA_ATTEMPT,
    METADATA_IMAGE_DIGEST,
    METADATA_RUN_ID,
    METADATA_RUNS_BUCKET,
)

_ATTRIBUTES = "instance/attributes/"
_INSTANCE_NAME = "instance/name"
# ``projects/<number>/zones/<zone>``: the zone is the last segment.
_INSTANCE_ZONE = "instance/zone"
# ``projects/<number>/machineTypes/<type>``: the type is the last segment.
_INSTANCE_MACHINE_TYPE = "instance/machine-type"
# ``TRUE`` on a spot (or legacy preemptible) VM.
_INSTANCE_PREEMPTIBLE = "instance/scheduling/preemptible"
_PROJECT_ID = "project/project-id"


@dataclass(frozen=True, slots=True)
class HostFacts:
    """The run this VM serves and the VM itself, as the metadata server reports them."""

    run_id: str
    attempt: int
    runs_bucket: str
    image_digest: str
    instance: str
    zone: str
    project: str
    machine_type: str
    spot: bool

    @property
    def run_prefix_uri(self) -> str:
        return f"gs://{self.runs_bucket}/{self.run_id}/"


def read_host_facts(cloud: BenchCloud) -> HostFacts:
    """Read every fact the supervisor needs; ``SupervisorRefused`` names the first one missing."""
    attempt = _required(cloud, _ATTRIBUTES + METADATA_ATTEMPT)
    if not attempt.isdigit() or int(attempt) < 1:
        raise SupervisorRefused(
            f"instance metadata {METADATA_ATTEMPT}: got {attempt!r}, expected a positive integer"
        )
    return HostFacts(
        run_id=_required(cloud, _ATTRIBUTES + METADATA_RUN_ID),
        attempt=int(attempt),
        runs_bucket=_required(cloud, _ATTRIBUTES + METADATA_RUNS_BUCKET),
        image_digest=_required(cloud, _ATTRIBUTES + METADATA_IMAGE_DIGEST),
        instance=_required(cloud, _INSTANCE_NAME),
        zone=_last_segment(_required(cloud, _INSTANCE_ZONE)),
        project=_required(cloud, _PROJECT_ID),
        machine_type=_last_segment(_required(cloud, _INSTANCE_MACHINE_TYPE)),
        spot=(cloud.read_instance_metadata(_INSTANCE_PREEMPTIBLE) or "").upper() == "TRUE",
    )


def _required(cloud: BenchCloud, path: str) -> str:
    value = (cloud.read_instance_metadata(path) or "").strip()
    if not value:
        raise SupervisorRefused(
            f"instance metadata {path}: got nothing, expected the value the launcher "
            "or Compute Engine sets on a bench VM"
        )
    return value


def _last_segment(value: str) -> str:
    return value.rsplit("/", 1)[-1]
