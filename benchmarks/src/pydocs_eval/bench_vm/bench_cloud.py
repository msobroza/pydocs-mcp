"""``BenchCloud``: the one cloud port the bench VM launcher and supervisor share.

Two implementations: ``GcloudBenchCloud`` shells out to the ``gcloud`` CLI (the
way the agent track shells out to ``claude``), and the tests' ``FakeBenchCloud``
keeps the buckets in a local directory and records every request.

This port carries the operations the launcher needs: object reads and
generation-checked writes on the buckets, machine-type facts, an image-tag
lookup and an image build, VM creation, and VM listing by label across zones
(the relaunch's live-VM check). The supervisor adds the ones it drives from
inside the run's container: directory syncs, the state restore on a relaunch
(a directory pull), instance-metadata reads and deleting its own VM.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydocs_eval.bench_vm.image_build_request import ImageBuildRequest
from pydocs_eval.bench_vm.vm_request import VmCreateRequest


@dataclass(frozen=True, slots=True)
class StoredObject:
    """An object's bytes and the GCS generation they were read at."""

    data: bytes
    generation: int


@dataclass(frozen=True, slots=True)
class MachineTypeFacts:
    """What the Compute API reports about a machine type in a zone.

    ``architecture`` is the API's spelling (``X86_64``, ``ARM64``), ``None``
    when it reports none. ``bundled_accelerators`` are the GPUs an
    accelerator-optimized type (``g2-…``) carries by itself, as
    ``(accelerator type, count)`` pairs.
    """

    name: str
    architecture: str | None
    guest_cpus: int
    memory_mb: int
    bundled_accelerators: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class ListedInstance:
    """A VM the Compute API listed: its name, its zone and its status (``RUNNING``, …)."""

    name: str
    zone: str
    status: str


class BenchCloudError(RuntimeError):
    """A cloud call failed; the message names the call and the cloud's own words."""


class StaleGenerationError(BenchCloudError):
    """A generation-checked write lost: the object is not at the generation the writer read."""

    def __init__(self, uri: str, *, expected: int, actual: int | None) -> None:
        self.uri = uri
        self.expected = expected
        self.actual = actual
        found = "an unknown generation" if actual is None else f"generation {actual}"
        super().__init__(
            f"stale write to {uri}: expected generation {expected} "
            f"(0 = the object must not exist), found {found}; another writer moved it"
        )


class ZoneStockoutError(BenchCloudError):
    """The zone has no capacity for the request; the only error that moves a launch on."""

    def __init__(self, zone: str, detail: str) -> None:
        self.zone = zone
        self.detail = detail
        super().__init__(f"zone {zone} has no capacity for the request: {detail}")


class ImageBuildError(BenchCloudError):
    """A Cloud Build build ended any way but success, or outlived the launcher's wait."""

    def __init__(
        self, image: str, *, build_id: str | None, log_url: str | None, status: str, detail: str
    ) -> None:
        self.image = image
        self.build_id = build_id
        self.log_url = log_url
        self.status = status
        build = "build" if build_id is None else f"build {build_id}"
        log = "no log location reported" if log_url is None else f"log: {log_url}"
        super().__init__(f"Cloud Build {build} of {image} ended {status}: {detail}; {log}")


class BenchCloud(Protocol):
    """The cloud operations the launcher and the supervisor drive."""

    def read_object(self, uri: str) -> StoredObject | None:
        """The object at ``gs://bucket/name`` and its generation, ``None`` when absent."""
        ...

    def write_object(self, uri: str, data: bytes, *, if_generation_match: int) -> int:
        """Write ``data`` only if the object is at ``if_generation_match`` (0: absent).

        Returns the new generation; raises ``StaleGenerationError`` otherwise.
        """
        ...

    def describe_machine_type(
        self, project: str, zone: str, machine_type: str
    ) -> MachineTypeFacts | None:
        """The machine type's facts in ``zone``, ``None`` when the zone has no such type."""
        ...

    def find_image_digest(self, image: str) -> str | None:
        """The ``sha256:…`` digest of the tagged ``image``, ``None`` when the tag is missing."""
        ...

    def build_image(self, request: ImageBuildRequest) -> None:
        """Submit the build and wait for it, at most ``request.wait_timeout_s``.

        Returns once the build succeeded; ``ImageBuildError`` otherwise. The
        caller reads the new tag's digest with ``find_image_digest``.
        """
        ...

    def create_instance(self, request: VmCreateRequest) -> None:
        """Create the VM; ``ZoneStockoutError`` when the zone has no capacity."""
        ...

    def list_instances(
        self, project: str, zones: Sequence[str], *, label: str, value: str
    ) -> tuple[ListedInstance, ...]:
        """Every VM in ``zones`` whose label ``label`` is exactly ``value``, in any status."""
        ...

    # --- the supervisor's side, from inside the run's container ----------
    def sync_directory(self, source: Path, destination_uri: str, *, exclude_pattern: str) -> None:
        """Mirror ``source`` under ``destination_uri`` incrementally; never delete in GCS.

        Files whose source-relative POSIX path matches ``exclude_pattern``
        (a Python regex, ``re.match``-anchored) are skipped.
        """
        ...

    def restore_directory(self, source_uri: str, destination: Path) -> None:
        """Pull every object under ``source_uri`` into ``destination``, creating it.

        A prefix with no objects restores nothing; it is not an error (a run
        whose earlier attempt never synced a file).
        """
        ...

    def read_instance_metadata(self, path: str) -> str | None:
        """The metadata server's value at ``path`` (``instance/zone``…), ``None`` when unset."""
        ...

    def delete_instance(self, project: str, zone: str, name: str) -> None:
        """Delete the VM ``name`` in ``zone``: how a finished run removes its own VM."""
        ...
