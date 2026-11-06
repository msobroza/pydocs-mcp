"""Named fakes for the bench VM's external I/O: the cloud, the deployment's names, gcloud, the clock.

``FakeBenchCloud`` is the in-process implementation of the ``BenchCloud`` port
the launcher (and, later, the supervisor) drives. It keeps the buckets in a
local directory, records every request it receives, and can be scripted to
fail the way the cloud does: a zone stockout, a stale generation, a missing
image tag (simply an image it was never given), or any other create error.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import (
    MachineTypeFacts,
    StaleGenerationError,
    StoredObject,
    ZoneStockoutError,
)
from pydocs_eval.bench_vm.bounded_command import CompletedCommand
from pydocs_eval.bench_vm.deployment import DeploymentNames
from pydocs_eval.bench_vm.vm_request import VmCreateRequest

FAKE_IMAGE_DIGEST = "sha256:" + "ab" * 32

N2_STANDARD_8 = MachineTypeFacts(
    name="n2-standard-8", architecture="X86_64", guest_cpus=8, memory_mb=32768
)
G2_STANDARD_8 = MachineTypeFacts(
    name="g2-standard-8",
    architecture="X86_64",
    guest_cpus=8,
    memory_mb=32768,
    bundled_accelerators=(("nvidia-l4", 1),),
)
N1_STANDARD_8 = MachineTypeFacts(
    name="n1-standard-8", architecture="X86_64", guest_cpus=8, memory_mb=30720
)
N2_STANDARD_4 = MachineTypeFacts(
    name="n2-standard-4", architecture="X86_64", guest_cpus=4, memory_mb=16384
)
N2_HIGHMEM_8 = MachineTypeFacts(
    name="n2-highmem-8", architecture="X86_64", guest_cpus=8, memory_mb=65536
)
T2A_STANDARD_8 = MachineTypeFacts(
    name="t2a-standard-8", architecture="ARM64", guest_cpus=8, memory_mb=32768
)
NO_ARCHITECTURE_8 = MachineTypeFacts(
    name="e2-standard-8", architecture=None, guest_cpus=8, memory_mb=32768
)
DEFAULT_MACHINE_TYPES = (
    N2_STANDARD_8,
    G2_STANDARD_8,
    N1_STANDARD_8,
    N2_STANDARD_4,
    N2_HIGHMEM_8,
    T2A_STANDARD_8,
    NO_ARCHITECTURE_8,
)


@dataclass(frozen=True, slots=True)
class FakeRequest:
    """One call the fake received: the operation and its arguments."""

    operation: str
    args: Mapping[str, object]


@dataclass
class FakeBenchCloud:
    """``BenchCloud`` over a local directory, recording every request.

    Script failures through the public fields: ``stockout_zones`` makes a create
    in those zones raise a stockout, ``create_errors`` maps a zone to any other
    error, ``machine_types_missing_in`` lists zones offering no machine type at
    all, ``stale_write_uris`` makes the next write of a URI lose its
    precondition race. An image tag is missing unless it is in ``images``.
    """

    root: Path
    machine_types: dict[str, MachineTypeFacts] = field(
        default_factory=lambda: {facts.name: facts for facts in DEFAULT_MACHINE_TYPES}
    )
    images: dict[str, str] = field(default_factory=dict)
    stockout_zones: set[str] = field(default_factory=set)
    create_errors: dict[str, Exception] = field(default_factory=dict)
    machine_types_missing_in: set[str] = field(default_factory=set)
    stale_write_uris: set[str] = field(default_factory=set)
    requests: list[FakeRequest] = field(default_factory=list)
    created: list[VmCreateRequest] = field(default_factory=list)
    _generations: dict[str, int] = field(default_factory=dict)
    _next_generation: int = 1_700_000_000_000_000

    # --- objects ---------------------------------------------------------
    def read_object(self, uri: str) -> StoredObject | None:
        self._record("read_object", uri=uri)
        path = self._path_of(uri)
        if not path.is_file():
            return None
        return StoredObject(data=path.read_bytes(), generation=self._generations[uri])

    def write_object(self, uri: str, data: bytes, *, if_generation_match: int) -> int:
        self._record("write_object", uri=uri, if_generation_match=if_generation_match)
        current = self._generations.get(uri, 0)
        if uri in self.stale_write_uris or current != if_generation_match:
            self.stale_write_uris.discard(uri)
            raise StaleGenerationError(uri, expected=if_generation_match, actual=current)
        path = self._path_of(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._next_generation += 1
        self._generations[uri] = self._next_generation
        return self._next_generation

    def put_object(self, uri: str, data: bytes) -> int:
        """Seed an object as if another writer had put it there."""
        return self.write_object(uri, data, if_generation_match=self._generations.get(uri, 0))

    def object_data(self, uri: str) -> bytes | None:
        path = self._path_of(uri)
        return path.read_bytes() if path.is_file() else None

    # --- compute and registry --------------------------------------------
    def describe_machine_type(
        self, project: str, zone: str, machine_type: str
    ) -> MachineTypeFacts | None:
        self._record("describe_machine_type", project=project, zone=zone, machine_type=machine_type)
        if zone in self.machine_types_missing_in:
            return None
        return self.machine_types.get(machine_type)

    def find_image_digest(self, image: str) -> str | None:
        self._record("find_image_digest", image=image)
        return self.images.get(image)

    def create_instance(self, request: VmCreateRequest) -> None:
        self._record("create_instance", zone=request.zone, name=request.name)
        if request.zone in self.stockout_zones:
            raise ZoneStockoutError(request.zone, "ZONE_RESOURCE_POOL_EXHAUSTED (scripted)")
        if request.zone in self.create_errors:
            raise self.create_errors[request.zone]
        self.created.append(request)

    # --- observation -----------------------------------------------------
    def operations(self) -> list[str]:
        return [request.operation for request in self.requests]

    def create_attempts(self) -> list[str]:
        return [str(r.args["zone"]) for r in self.requests if r.operation == "create_instance"]

    def _record(self, operation: str, **args: object) -> None:
        self.requests.append(FakeRequest(operation=operation, args=args))

    def _path_of(self, uri: str) -> Path:
        if not uri.startswith("gs://"):
            raise ValueError(f"invalid object uri: got {uri!r}, expected gs://<bucket>/<name>")
        return self.root / uri.removeprefix("gs://")


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


@dataclass(frozen=True, slots=True)
class CommandCall:
    """One command the runner was asked to run.

    ``metadata_files`` holds the contents of every ``--metadata-from-file``
    file as the command would have read them, since the adapter removes them
    once the command returns.
    """

    argv: tuple[str, ...]
    stdin: bytes | None
    timeout_s: float
    env: Mapping[str, str]
    metadata_files: Mapping[str, str]


def _metadata_files_named_by(argv: Sequence[str]) -> dict[str, str]:
    prefix = "--metadata-from-file="
    contents: dict[str, str] = {}
    for arg in argv:
        if arg.startswith(prefix):
            for pair in arg.removeprefix(prefix).split(","):
                key, path = pair.split("=", 1)
                contents[key] = Path(path).read_text(encoding="utf-8")
    return contents


@dataclass
class ScriptedCommandRunner:
    """Stands in for running ``gcloud`` / ``terraform``: replays scripted results, records calls.

    Each script entry is a ``CompletedCommand`` or ``"timeout"``; the runner
    replays them in order and repeats the last one when it runs out.
    """

    script: list[CompletedCommand | str]
    calls: list[CommandCall] = field(default_factory=list)

    def __call__(
        self, argv: Sequence[str], *, stdin: bytes | None, timeout_s: float, env: Mapping[str, str]
    ) -> CompletedCommand:
        self.calls.append(
            CommandCall(tuple(argv), stdin, timeout_s, dict(env), _metadata_files_named_by(argv))
        )
        outcome = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(list(argv), timeout_s)
        assert isinstance(outcome, CompletedCommand)
        return outcome


def ok(stdout: str = "") -> CompletedCommand:
    return CompletedCommand(returncode=0, stdout=stdout.encode(), stderr=b"")


def failed(stderr: str, returncode: int = 1) -> CompletedCommand:
    return CompletedCommand(returncode=returncode, stdout=b"", stderr=stderr.encode())
