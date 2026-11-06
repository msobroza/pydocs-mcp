"""Named fakes for the bench VM's external I/O: the cloud, the deployment's names, gcloud, the clock.

``FakeBenchCloud`` is the in-process implementation of the ``BenchCloud`` port
the launcher and the supervisor drive. It keeps the buckets in a
local directory, records every request it receives, and can be scripted to
fail the way the cloud does: a zone stockout, a stale generation, a missing
image tag (simply an image it was never given), a failed build, a build that
loses the immutable-tag race to a concurrent one, or any other create error.
A VM it created stays listed until a test makes it vanish (a self-delete, a
preemption, the maximum run duration).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import tarfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import (
    BenchCloudError,
    ImageBuildError,
    ListedInstance,
    MachineTypeFacts,
    StaleGenerationError,
    StoredObject,
    ZoneStockoutError,
)
from pydocs_eval.bench_vm.bounded_command import CompletedCommand
from pydocs_eval.bench_vm.deployment import DeploymentNames
from pydocs_eval.bench_vm.gpu_preflight import CUDA_EXECUTION_PROVIDER, GpuProbe, GpuProbeError
from pydocs_eval.bench_vm.image_build_request import COMMIT_ARCHIVE_NAME, ImageBuildRequest
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


def fake_built_digest(image: str, *, builder: str = "this launch") -> str:
    """The digest the fake registry gives ``image`` when ``builder`` pushes it."""
    return "sha256:" + hashlib.sha256(f"{builder}:{image}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class FakeBuild:
    """One build the fake received, with its source read while it still existed.

    ``archive_commit`` is the commit id ``git archive`` embeds in the tar's pax
    header (what ``git get-tar-commit-id`` prints); ``config_text`` is the
    Cloud Build config the build was submitted with.
    """

    request: ImageBuildRequest
    source_files: tuple[str, ...]
    archive_commit: str | None
    archive_members: frozenset[str]
    config_text: str


def _read_build_source(request: ImageBuildRequest) -> FakeBuild:
    source_files = tuple(sorted(path.name for path in request.source_dir.iterdir()))
    with tarfile.open(request.source_dir / COMMIT_ARCHIVE_NAME) as archive:
        commit = archive.pax_headers.get("comment")
        members = frozenset(member.name for member in archive.getmembers() if member.isfile())
    return FakeBuild(
        request=request,
        source_files=source_files,
        archive_commit=commit,
        archive_members=members,
        config_text=request.config.read_text(encoding="utf-8"),
    )


@dataclass(frozen=True, slots=True)
class FakeRequest:
    """One call the fake received: the operation and its arguments."""

    operation: str
    args: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class FakeSync:
    """One directory sync: what was mirrored where, and which files it uploaded, in order."""

    source: Path
    destination: str
    uploaded: tuple[str, ...]


def _relative_files(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


@dataclass
class FakeBenchCloud:
    """``BenchCloud`` over a local directory, recording every request.

    Script failures through the public fields: ``stockout_zones`` makes a create
    in those zones raise a stockout, ``create_errors`` maps a zone to any other
    error, ``machine_types_missing_in`` lists zones offering no machine type at
    all, ``stale_write_uris`` makes the next write of a URI lose its
    precondition race. An image tag is missing unless it is in ``images``; a
    build of an image in ``failing_builds`` fails, and one in ``racing_builds``
    loses its push to a concurrent build of the same tag (which then exists).
    ``vanish`` removes created VMs from the listing.
    On the supervisor's side, ``metadata_errors`` makes a metadata read raise and
    ``lost_answer_uris`` makes the next write of a URI land but raise, the way a
    write whose follow-up describe failed does. ``secrets`` is Secret Manager,
    keyed by secret id; a read records the secret id, never the value.
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
    failing_builds: set[str] = field(default_factory=set)
    racing_builds: set[str] = field(default_factory=set)
    requests: list[FakeRequest] = field(default_factory=list)
    created: list[VmCreateRequest] = field(default_factory=list)
    builds: list[FakeBuild] = field(default_factory=list)
    vanished: set[str] = field(default_factory=set)
    # The metadata server, keyed by path (``instance/zone``, ``instance/attributes/…``).
    instance_metadata: dict[str, str] = field(default_factory=dict)
    metadata_errors: dict[str, Exception] = field(default_factory=dict)
    # The next write of each URI lands, but its answer is lost (raises).
    lost_answer_uris: set[str] = field(default_factory=set)
    syncs: list[FakeSync] = field(default_factory=list)
    deleted: list[tuple[str, str, str]] = field(default_factory=list)
    secrets: dict[str, str] = field(default_factory=dict)
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
        if uri in self.lost_answer_uris:
            self.lost_answer_uris.discard(uri)
            raise BenchCloudError(f"writing {uri}: the answer was lost (scripted)")
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

    def build_image(self, request: ImageBuildRequest) -> None:
        self._record("build_image", image=request.image, region=request.region)
        build = _read_build_source(request)
        self.builds.append(build)
        build_id = f"fake-build-{len(self.builds)}"
        log_url = f"https://console.cloud.google.com/cloud-build/builds;region={request.region}/{build_id}"
        if request.image in self.failing_builds:
            raise ImageBuildError(
                request.image,
                build_id=build_id,
                log_url=log_url,
                status="FAILURE",
                detail="step smoke exited 1 (scripted)",
            )
        if request.image in self.racing_builds:
            self.images[request.image] = fake_built_digest(request.image, builder="a rival launch")
            raise ImageBuildError(
                request.image,
                build_id=build_id,
                log_url=log_url,
                status="FAILURE",
                detail="push refused: the tag already exists and tags are immutable (scripted)",
            )
        self.images[request.image] = fake_built_digest(request.image)

    def create_instance(self, request: VmCreateRequest) -> None:
        self._record("create_instance", zone=request.zone, name=request.name)
        if request.zone in self.stockout_zones:
            raise ZoneStockoutError(request.zone, "ZONE_RESOURCE_POOL_EXHAUSTED (scripted)")
        if request.zone in self.create_errors:
            raise self.create_errors[request.zone]
        self.created.append(request)

    def list_instances(
        self, project: str, zones: Sequence[str], *, label: str, value: str
    ) -> tuple[ListedInstance, ...]:
        self._record(
            "list_instances", project=project, zones=tuple(zones), label=label, value=value
        )
        return tuple(
            ListedInstance(name=request.name, zone=request.zone, status="RUNNING")
            for request in self.created
            if request.zone in zones
            and request.labels.get(label) == value
            and request.name not in self.vanished
        )

    def vanish(self, *names: str) -> None:
        """The VMs are gone (self-delete, preemption, max run duration); all of them by default."""
        self.vanished.update(names or (request.name for request in self.created))

    # --- the supervisor's side -------------------------------------------
    def sync_directory(self, source: Path, destination_uri: str, *, exclude_pattern: str) -> None:
        """Copy every new or changed file not matching ``exclude_pattern``; delete nothing."""
        uploaded = tuple(
            relative
            for relative in _relative_files(source)
            if not re.match(exclude_pattern, relative)
            and self._upload_if_changed(source / relative, destination_uri + relative)
        )
        self._record("sync_directory", source=str(source), destination=destination_uri)
        self.syncs.append(FakeSync(source=source, destination=destination_uri, uploaded=uploaded))

    def _upload_if_changed(self, local: Path, uri: str) -> bool:
        data = local.read_bytes()
        if self.object_data(uri) == data:
            return False
        self.put_object(uri, data)
        return True

    def read_instance_metadata(self, path: str) -> str | None:
        self._record("read_instance_metadata", path=path)
        if path in self.metadata_errors:
            raise self.metadata_errors[path]
        return self.instance_metadata.get(path)

    def delete_instance(self, project: str, zone: str, name: str) -> None:
        self._record("delete_instance", project=project, zone=zone, name=name)
        self.deleted.append((project, zone, name))
        # A deleted VM no longer lists, as after any other way a VM goes.
        self.vanished.add(name)

    def access_secret(self, project: str, secret_id: str) -> str:
        self._record("access_secret", project=project, secret_id=secret_id)
        if secret_id not in self.secrets:
            raise BenchCloudError(f"reading secret {secret_id} in {project}: NOT_FOUND (scripted)")
        return self.secrets[secret_id]

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


# --- the GPU pre-flight's probe -------------------------------------------------------

CUDA_READY_PROBE = GpuProbe(
    torch_version="2.5.1+cu124",
    torch_sees_cuda=True,
    torch_error=None,
    onnxruntime_providers=(CUDA_EXECUTION_PROVIDER, "CPUExecutionProvider"),
    onnxruntime_error=None,
)
CPU_ONLY_PROBE = GpuProbe(
    torch_version="2.5.1+cpu",
    torch_sees_cuda=False,
    torch_error=None,
    onnxruntime_providers=("CPUExecutionProvider",),
    onnxruntime_error=None,
)


@dataclass
class FakeGpuProbe:
    """Stands in for asking the image's interpreter: answers ``probe`` (or raises).

    Records the interpreter each call asked; ``delay_s`` makes it slow.
    """

    probe: GpuProbe = CUDA_READY_PROBE
    error: str | None = None
    delay_s: float = 0.0
    asked: list[tuple[str, ...]] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return len(self.asked)

    def __call__(self, python: Sequence[str]) -> GpuProbe:
        self.asked.append(tuple(python))
        time.sleep(self.delay_s)
        if self.error is not None:
            raise GpuProbeError(self.error)
        return self.probe
