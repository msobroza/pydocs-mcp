"""The ``gcloud compute instances create`` arguments of a ``VmCreateRequest``.

The metadata values (the host scripts among them) travel as files, one per
key, because they hold commas and newlines a flag value cannot.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydocs_eval.bench_vm.vm_request import VmCreateRequest


def write_metadata_files(request: VmCreateRequest, scratch: Path) -> dict[str, Path]:
    """One file per metadata value: scripts hold commas and newlines a flag value cannot."""
    files = {}
    for index, (key, value) in enumerate(request.metadata.items()):
        path = scratch / f"{index}.txt"
        path.write_text(value, encoding="utf-8")
        files[key] = path
    return files


def instance_create_argv(
    request: VmCreateRequest, metadata_files: Mapping[str, Path]
) -> tuple[str, ...]:
    """The ``gcloud compute instances create`` arguments for ``request`` (after ``gcloud``)."""
    return (
        "compute",
        "instances",
        "create",
        request.name,
        f"--project={request.project}",
        f"--zone={request.zone}",
        f"--machine-type={request.machine_type}",
        *_scheduling_flags(request),
        *_network_and_identity_flags(request),
        *_boot_disk_flags(request),
        *_labels_and_metadata_flags(request, metadata_files),
        *_gpu_flags(request),
        "--format=json",
    )


def _scheduling_flags(request: VmCreateRequest) -> tuple[str, ...]:
    # The maximum run duration and its DELETE action are the backstop for a hung guest.
    return (
        f"--provisioning-model={request.provisioning_model}",
        f"--max-run-duration={request.max_run_duration_s}s",
        f"--instance-termination-action={request.termination_action}",
    )


def _network_and_identity_flags(request: VmCreateRequest) -> tuple[str, ...]:
    address = () if request.external_ip else ("--no-address",)
    return (
        *address,
        f"--subnet={request.subnetwork}",
        f"--service-account={request.service_account}",
        "--scopes=cloud-platform",
    )


def _boot_disk_flags(request: VmCreateRequest) -> tuple[str, ...]:
    auto_delete = (
        "--boot-disk-auto-delete"
        if request.boot_disk_auto_delete
        else ("--no-boot-disk-auto-delete")
    )
    return (
        f"--image={request.boot_image}",
        f"--boot-disk-size={request.boot_disk_gb}GB",
        auto_delete,
    )


def _labels_and_metadata_flags(
    request: VmCreateRequest, metadata_files: Mapping[str, Path]
) -> tuple[str, ...]:
    labels = ",".join(f"{key}={value}" for key, value in request.labels.items())
    metadata = ",".join(f"{key}={path}" for key, path in metadata_files.items())
    return (f"--labels={labels}", f"--metadata-from-file={metadata}")


def _gpu_flags(request: VmCreateRequest) -> tuple[str, ...]:
    gpu = request.gpu
    if gpu is None:
        return ()
    # A GPU VM cannot live-migrate, so host maintenance must terminate it.
    terminate = ("--maintenance-policy=TERMINATE",)
    if gpu.attached_by_machine_type:
        return terminate
    return (f"--accelerator=type={gpu.accelerator_type},count={gpu.count}", *terminate)
