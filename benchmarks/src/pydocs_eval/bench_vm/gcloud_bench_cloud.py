"""``GcloudBenchCloud``: the ``BenchCloud`` port over the ``gcloud`` CLI.

It shells out the way the agent track shells out to ``claude``: no new Python
dependency, and the calls run as the owner's ``gcloud auth login`` session —
the environment it passes drops every credential-file override, and no key
file is read, written or referenced. Every call has a timeout and a bounded
retry on transient errors (``bounded_command.run_bounded``).

gcloud reports "not found", "precondition failed" and "no capacity" only in
its stderr, so those three answers are recognised by their documented markers;
anything else is a ``BenchCloudError`` carrying gcloud's own words.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydocs_eval.bench_vm.bench_cloud import (
    BenchCloudError,
    MachineTypeFacts,
    StaleGenerationError,
    StoredObject,
    ZoneStockoutError,
)
from pydocs_eval.bench_vm.bounded_command import (
    BoundedCommandError,
    CommandRunner,
    CompletedCommand,
    run_bounded,
    run_subprocess,
)
from pydocs_eval.bench_vm.vm_request import VmCreateRequest

_READ_TIMEOUT_S = 60.0
_WRITE_TIMEOUT_S = 120.0
# Instance creation waits for the operation, which can take minutes on a GPU.
_CREATE_TIMEOUT_S = 600.0

_NOT_FOUND = re.compile(r"(?i)not.?found|matched no objects|no urls matched|\b404\b")
_PRECONDITION_FAILED = re.compile(r"(?i)pre-?conditions?\b.*fail|conditionNotMet|\b412\b")
_STOCKOUT = re.compile(
    r"ZONE_RESOURCE_POOL_EXHAUSTED|does not have enough resources available|STOCKOUT"
)
_ALREADY_EXISTS = re.compile(r"(?i)already ?exists")


@dataclass
class _CountingRunner:
    """Counts the attempts ``run_bounded`` makes, so create can tell a retry from a first try."""

    runner: CommandRunner
    calls: int = 0

    def __call__(
        self, argv: Sequence[str], *, stdin: bytes | None, timeout_s: float, env: Mapping[str, str]
    ) -> CompletedCommand:
        self.calls += 1
        return self.runner(argv, stdin=stdin, timeout_s=timeout_s, env=env)


@dataclass(frozen=True, slots=True)
class GcloudBenchCloud:
    """``BenchCloud`` through the ``gcloud`` CLI on the operator's machine."""

    runner: CommandRunner = run_subprocess
    sleep: Callable[[float], None] = field(default=time.sleep)

    # --- objects ---------------------------------------------------------
    def read_object(self, uri: str) -> StoredObject | None:
        generation = self._generation_of(uri)
        if generation is None:
            return None
        # Read the generation just described, so data and generation agree.
        completed = self._gcloud(("storage", "cat", f"{uri}#{generation}"), _READ_TIMEOUT_S)
        self._raise_unless_ok(completed, f"reading {uri}")
        return StoredObject(data=completed.stdout, generation=generation)

    def write_object(self, uri: str, data: bytes, *, if_generation_match: int) -> int:
        argv = ("storage", "cp", "-", uri, f"--if-generation-match={if_generation_match}")
        counting = _CountingRunner(self.runner)
        completed = self._gcloud(argv, _WRITE_TIMEOUT_S, stdin=data, runner=counting)
        if completed.returncode != 0 and _PRECONDITION_FAILED.search(completed.stderr_text):
            return self._landed_generation(
                uri, data, if_generation_match, retried=counting.calls > 1
            )
        self._raise_unless_ok(completed, f"writing {uri}")
        generation = self._generation_of(uri)
        if generation is None:
            raise BenchCloudError(f"writing {uri}: the object is missing right after the write")
        return generation

    def _landed_generation(self, uri: str, data: bytes, expected: int, *, retried: bool) -> int:
        """A retry's precondition failure is ours when an earlier attempt landed these bytes."""
        stored = self.read_object(uri) if retried else None
        if stored is not None and stored.data == data:
            return stored.generation
        actual = None if stored is None else stored.generation
        raise StaleGenerationError(uri, expected=expected, actual=actual)

    def _generation_of(self, uri: str) -> int | None:
        argv = ("storage", "objects", "describe", uri, "--format=json")
        answer = self._json_or_none(argv, f"describing {uri}")
        if answer is None:
            return None
        generation = answer.get("generation")
        if not str(generation).isdigit():
            raise BenchCloudError(f"{uri}: got generation {generation!r}, expected an integer")
        return int(str(generation))

    # --- compute and registry --------------------------------------------
    def describe_machine_type(
        self, project: str, zone: str, machine_type: str
    ) -> MachineTypeFacts | None:
        argv = (
            "compute",
            "machine-types",
            "describe",
            machine_type,
            f"--zone={zone}",
            f"--project={project}",
            "--format=json",
        )
        answer = self._json_or_none(argv, f"describing machine type {machine_type} in {zone}")
        return None if answer is None else _machine_type_facts(answer)

    def find_image_digest(self, image: str) -> str | None:
        argv = ("artifacts", "docker", "images", "describe", image, "--format=json")
        answer = self._json_or_none(argv, f"describing image {image}")
        if answer is None:
            return None
        digest = answer.get("image_summary", {}).get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise BenchCloudError(f"image {image}: got digest {digest!r}, expected sha256:<hex>")
        return digest

    def create_instance(self, request: VmCreateRequest) -> None:
        with tempfile.TemporaryDirectory(prefix="bench-vm-metadata-") as scratch:
            argv = instance_create_argv(request, _metadata_files(request, Path(scratch)))
            counting = _CountingRunner(self.runner)
            completed = self._gcloud(argv, _CREATE_TIMEOUT_S, runner=counting)
        if completed.returncode == 0:
            return
        stderr = completed.stderr_text
        if _STOCKOUT.search(stderr):
            raise ZoneStockoutError(request.zone, stderr)
        # A retried create whose first attempt landed answers "already exists".
        if counting.calls > 1 and _ALREADY_EXISTS.search(stderr):
            return
        raise BenchCloudError(f"creating {request.name} in {request.zone} failed: {stderr}")

    # --- plumbing --------------------------------------------------------
    def _gcloud(
        self,
        args: Sequence[str],
        timeout_s: float,
        *,
        stdin: bytes | None = None,
        runner: CommandRunner | None = None,
    ) -> CompletedCommand:
        argv = ("gcloud", *args, "--quiet")
        try:
            return run_bounded(
                runner or self.runner, argv, timeout_s=timeout_s, stdin=stdin, sleep=self.sleep
            )
        except BoundedCommandError as exc:
            raise BenchCloudError(str(exc)) from None
        except OSError as exc:  # gcloud not installed or not executable
            raise BenchCloudError(
                f"running gcloud failed: {exc}; is the Cloud SDK on PATH?"
            ) from None

    def _json_or_none(self, args: Sequence[str], what: str) -> dict[str, Any] | None:
        completed = self._gcloud(args, _READ_TIMEOUT_S)
        if completed.returncode != 0 and _NOT_FOUND.search(completed.stderr_text):
            return None
        self._raise_unless_ok(completed, what)
        try:
            answer = json.loads(completed.stdout or b"{}")
        except json.JSONDecodeError as exc:
            raise BenchCloudError(
                f"{what}: got unreadable JSON ({exc}), expected an object"
            ) from None
        if not isinstance(answer, dict):
            raise BenchCloudError(f"{what}: got {answer!r}, expected a JSON object")
        return answer

    @staticmethod
    def _raise_unless_ok(completed: CompletedCommand, what: str) -> None:
        if completed.returncode != 0:
            raise BenchCloudError(
                f"{what} failed (exit {completed.returncode}): {completed.stderr_text}"
            )


def _machine_type_facts(answer: Mapping[str, Any]) -> MachineTypeFacts:
    accelerators = answer.get("accelerators") or []
    architecture = answer.get("architecture")
    return MachineTypeFacts(
        name=str(answer["name"]),
        architecture=architecture if isinstance(architecture, str) else None,
        guest_cpus=int(answer["guestCpus"]),
        memory_mb=int(answer["memoryMb"]),
        bundled_accelerators=tuple(
            (str(entry["guestAcceleratorType"]), int(entry["guestAcceleratorCount"]))
            for entry in accelerators
        ),
    )


def _metadata_files(request: VmCreateRequest, scratch: Path) -> dict[str, Path]:
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
