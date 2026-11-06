"""The VM create request: the one shape every bench VM is created with.

``build_vm_create_request`` is pure: the launcher computes the request (for a
real launch or a dry run's plan) and hands it to ``BenchCloud.create_instance``.
The label and metadata keys are the contract with the host startup script and
the supervisor, which read them back from the instance.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

LABEL_RUN_ID = "bench-run-id"
LABEL_ATTEMPT = "bench-attempt"

METADATA_RUN_ID = "bench-run-id"
METADATA_ATTEMPT = "bench-attempt"
METADATA_RUNS_BUCKET = "bench-runs-bucket"
METADATA_CACHE_BUCKET = "bench-cache-bucket"
# The image the startup script pulls, by digest: <repository>/bench-<flavour>@sha256:…
METADATA_IMAGE = "bench-image"
METADATA_IMAGE_DIGEST = "bench-image-digest"
# Compute Engine's own keys: the guest agent runs these at boot and at shutdown.
METADATA_STARTUP_SCRIPT = "startup-script"
METADATA_SHUTDOWN_SCRIPT = "shutdown-script"


class ImageFlavour(StrEnum):
    """Which image a run needs: ``bench-cpu:<sha>`` or ``bench-cuda:<sha>``."""

    CPU = "cpu"
    CUDA = "cuda"


class ProvisioningModel(StrEnum):
    """Compute Engine's provisioning models, in its spelling."""

    SPOT = "SPOT"
    STANDARD = "STANDARD"


class TerminationAction(StrEnum):
    """What the platform does at the maximum run duration (and to a preempted spot VM)."""

    DELETE = "DELETE"


@dataclass(frozen=True, slots=True)
class GpuAttachment:
    """The run's GPUs. ``attached_by_machine_type``: the type (``g2-…``) carries them itself."""

    accelerator_type: str
    count: int
    attached_by_machine_type: bool


@dataclass(frozen=True, slots=True)
class VmCreateRequest:
    """Everything one bench VM is created with."""

    project: str
    zone: str
    name: str
    machine_type: str
    provisioning_model: ProvisioningModel
    max_run_duration_s: int
    termination_action: TerminationAction
    # Always False: a bench VM egresses through Cloud NAT and is reached over IAP.
    external_ip: bool
    service_account: str
    subnetwork: str
    boot_image: str
    boot_disk_gb: int
    boot_disk_auto_delete: bool
    gpu: GpuAttachment | None
    labels: Mapping[str, str]
    metadata: Mapping[str, str]


def image_tag_name(repository: str, flavour: ImageFlavour, commit: str) -> str:
    """``<repository>/bench-<flavour>:<commit>``, the immutable tag a build pushes."""
    return f"{repository}/bench-{flavour}:{commit}"


def image_digest_name(repository: str, flavour: ImageFlavour, digest: str) -> str:
    """``<repository>/bench-<flavour>@<digest>``, what the host pulls."""
    return f"{repository}/bench-{flavour}@{digest}"


def vm_labels(run_id: str, attempt: int) -> dict[str, str]:
    return {LABEL_RUN_ID: run_id, LABEL_ATTEMPT: str(attempt)}
