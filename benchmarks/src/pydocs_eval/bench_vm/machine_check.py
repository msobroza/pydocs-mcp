"""ADR 0014's machine floor, checked against the facts the Compute API reports.

A bench VM must be x86_64 with at least 8 vCPU and 32 GB (32768 MB, what
``n2-standard-8`` reports). The check runs before anything is written, on the
machine type's facts in the first zone of the launch order that offers it.

The Compute API reports no architecture for the classic x86 families
(``n2-standard-8``, ``e2-standard-8``) and one for the newer families:
``X86_64`` for ``g2-standard-8``, ``ARM64`` for the Arm types ``t2a-standard-8``
and ``c4a-standard-8`` (asked in europe-west4-a on 2026-11-17, #434). So a
type that reports no architecture counts as x86_64, and on architecture the
check refuses only a type that reports one other than ``X86_64``.
"""

from __future__ import annotations

from pydocs_eval.bench_vm.bench_cloud import MachineTypeFacts
from pydocs_eval.bench_vm.launch_refusal import LaunchRefused
from pydocs_eval.bench_vm.run_spec import GpuRequest
from pydocs_eval.bench_vm.vm_request import GpuAttachment

MIN_GUEST_CPUS = 8
MIN_MEMORY_MB = 32 * 1024
REQUIRED_ARCHITECTURE = "X86_64"


def refuse_unoffered_machine_type(machine_type: str, zones: tuple[str, ...]) -> LaunchRefused:
    return LaunchRefused(
        f"machine_type: got {machine_type!r}, expected a machine type one of the zones "
        f"{', '.join(zones)} offers; the Compute API reports no such machine type there"
    )


def check_machine_type(facts: MachineTypeFacts, gpu: GpuRequest | None) -> GpuAttachment | None:
    """Refuse a machine type under the floor; return how the run's GPU attaches to it."""
    _check_floor(facts)
    return _gpu_attachment(facts, gpu)


def _check_floor(facts: MachineTypeFacts) -> None:
    architecture, named = _architecture_as_read(facts)
    under_floor = (
        architecture != REQUIRED_ARCHITECTURE
        or facts.guest_cpus < MIN_GUEST_CPUS
        or facts.memory_mb < MIN_MEMORY_MB
    )
    if not under_floor:
        return
    raise LaunchRefused(
        f"machine_type: got {facts.name!r} ({facts.guest_cpus} vCPU, {facts.memory_mb} MB, "
        f"{named}), expected {REQUIRED_ARCHITECTURE} with at least {MIN_GUEST_CPUS} vCPU "
        f"and {MIN_MEMORY_MB} MB (ADR 0014's floor)"
    )


def _architecture_as_read(facts: MachineTypeFacts) -> tuple[str, str]:
    """The architecture the floor checks, and how a refusal names it.

    None reported reads as ``X86_64`` (the module docstring says why), and the
    refusal says so, so that a size refusal never seems to blame the architecture.
    """
    if facts.architecture is None:
        return REQUIRED_ARCHITECTURE, f"no architecture reported, read as {REQUIRED_ARCHITECTURE}"
    return facts.architecture, facts.architecture


def _gpu_attachment(facts: MachineTypeFacts, gpu: GpuRequest | None) -> GpuAttachment | None:
    bundled = facts.bundled_accelerators
    if not bundled:
        return None if gpu is None else GpuAttachment(str(gpu.type), gpu.count, False)
    wanted = () if gpu is None else ((str(gpu.type), gpu.count),)
    if tuple(bundled) != wanted:
        carried = ", ".join(f"{kind} x {count}" for kind, count in bundled)
        asked = "no gpu" if gpu is None else f"{gpu.type} x {gpu.count}"
        raise LaunchRefused(
            f"gpu: got {asked}, expected the {carried} machine type {facts.name!r} carries; "
            "set gpu to match it or pick another machine_type"
        )
    accelerator_type, count = bundled[0]
    return GpuAttachment(accelerator_type, count, attached_by_machine_type=True)
