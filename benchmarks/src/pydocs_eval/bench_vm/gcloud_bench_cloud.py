"""``GcloudBenchCloud``: the ``BenchCloud`` port over the ``gcloud`` CLI.

It shells out the way the agent track shells out to ``claude``: no new Python
dependency, and the calls run as the owner's ``gcloud auth login`` session on
the laptop, or as the VM's service account (from the metadata server) on a
bench VM — the environment it passes drops every credential-file override,
and no key file is read, written or referenced. Instance-metadata reads are
plain ``curl`` requests to the metadata server, under the same timeout and
bounded retry. Every call has a timeout and a bounded
retry on transient errors (``bounded_command.run_bounded``).

gcloud reports "not found", "precondition failed" and "no capacity" only in
its stderr, so those three answers are recognised by their documented markers;
anything else is a ``BenchCloudError`` carrying gcloud's own words.

The image builds (submitted ``--async``, then polled) live in
``gcloud_image_builds``, the cache bucket's operations in
``gcloud_cache_transfers``, and the instance-create arguments in
``gcloud_instance_create``.
"""

from __future__ import annotations

import re
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydocs_eval.bench_vm.bench_cloud import (
    BenchCloudError,
    LabelledInstance,
    ListedInstance,
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
from pydocs_eval.bench_vm.gcloud_cache_transfers import GcloudCacheTransfers
from pydocs_eval.bench_vm.gcloud_calls import GCLOUD_READ_TIMEOUT_S
from pydocs_eval.bench_vm.gcloud_image_builds import GcloudImageBuilds
from pydocs_eval.bench_vm.gcloud_instance_create import instance_create_argv, write_metadata_files
from pydocs_eval.bench_vm.gcloud_json import json_list, json_object
from pydocs_eval.bench_vm.gcloud_stderr import NO_OBJECTS_MATCHED, NOT_FOUND, PRECONDITION_FAILED
from pydocs_eval.bench_vm.vm_request import VmCreateRequest

_WRITE_TIMEOUT_S = 120.0
# Instance creation waits for the operation, which can take minutes on a GPU.
_CREATE_TIMEOUT_S = 600.0
# A state sync uploads whatever changed since the last one: ledgers, logs and
# reports. The supervisor's final-sync margin assumes this cap per attempt.
_SYNC_TIMEOUT_S = 300.0
# A relaunch pulls the whole state back, every attempt's logs included.
_RESTORE_TIMEOUT_S = 600.0
_DELETE_TIMEOUT_S = 300.0
# One small Secret Manager read per provider key the run's kind declares.
_SECRET_TIMEOUT_S = 60.0
# A pull brings a whole run home: result files, ledgers, reports and logs.
_DOWNLOAD_TIMEOUT_S = 1800.0
# The metadata server answers in milliseconds; curl's own cap sits under the
# call's, so curl reports the timeout rather than being killed.
_METADATA_TIMEOUT_S = 15.0
_METADATA_CURL_MAX_TIME_S = 10
_METADATA_SERVER_URL = "http://metadata.google.internal/computeMetadata/v1/"
# `curl -f` exits 22 on an HTTP error status; 404 means the key is unset.
_CURL_HTTP_404 = re.compile(r"returned error: 404\b")

_NOT_FOUND = NOT_FOUND
_PRECONDITION_FAILED = PRECONDITION_FAILED
_STOCKOUT = re.compile(
    r"ZONE_RESOURCE_POOL_EXHAUSTED|does not have enough resources available|STOCKOUT"
)
_ALREADY_EXISTS = re.compile(r"(?i)already ?exists")
# A suffix range over an empty object has no bytes to return: HTTP 416.
_RANGE_NOT_SATISFIABLE = re.compile(r"(?i)\b416\b|range.*not.*satisf|InvalidRange")
# Only an empty prefix restores nothing. A missing bucket or an object that
# 404s mid-transfer is a failure: a child started on an empty state would
# overwrite the synced ledgers at its first sync.
_NOTHING_TO_RESTORE = NO_OBJECTS_MATCHED


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
class GcloudBenchCloud(GcloudCacheTransfers, GcloudImageBuilds):
    """``BenchCloud`` through the ``gcloud`` CLI on the operator's machine.

    The cache bucket's operations come from ``GcloudCacheTransfers``, the image
    builds from ``GcloudImageBuilds``.
    """

    runner: CommandRunner = run_subprocess
    sleep: Callable[[float], None] = field(default=time.sleep)
    monotonic: Callable[[], float] = field(default=time.monotonic)

    # --- objects ---------------------------------------------------------
    def read_object(self, uri: str) -> StoredObject | None:
        generation = self._generation_of(uri)
        if generation is None:
            return None
        # Read the generation just described, so data and generation agree.
        completed = self._gcloud(("storage", "cat", f"{uri}#{generation}"), GCLOUD_READ_TIMEOUT_S)
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
            argv = instance_create_argv(request, write_metadata_files(request, Path(scratch)))
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

    # --- instance listing ------------------------------------------------
    def list_instances(
        self, project: str, zones: Sequence[str], *, label: str, value: str
    ) -> tuple[ListedInstance, ...]:
        items = self._instance_items(
            project, zones, f"labels.{label}={value}", f"listing instances labelled {label}={value}"
        )
        # The server-side filter is a pattern match; only an exact label value counts.
        return tuple(
            _listed_instance(item)
            for item in items
            if (item.get("labels") or {}).get(label) == value
        )

    def list_labelled_instances(
        self, project: str, zones: Sequence[str], *, label: str
    ) -> tuple[LabelledInstance, ...]:
        items = self._instance_items(
            project, zones, f"labels.{label}:*", f"listing instances carrying label {label}"
        )
        # Only a VM that really carries the label key counts, whatever the filter matched.
        return tuple(
            _labelled_instance(item) for item in items if label in (item.get("labels") or {})
        )

    def _instance_items(
        self, project: str, zones: Sequence[str], filter_expression: str, what: str
    ) -> list[dict[str, Any]]:
        argv = (
            "compute",
            "instances",
            "list",
            f"--project={project}",
            f"--zones={','.join(zones)}",
            f"--filter={filter_expression}",
            "--format=json",
        )
        completed = self._gcloud(argv, GCLOUD_READ_TIMEOUT_S)
        self._raise_unless_ok(completed, what)
        return json_list(completed.stdout, what)

    # --- the owner's run-control side: log tails, pulls --------------------
    def read_object_tail(self, uri: str, *, max_bytes: int) -> bytes | None:
        # A suffix range reads only the end of a log that can grow for hours.
        argv = ("storage", "cat", f"--range=-{max_bytes}", uri)
        completed = self._gcloud(argv, GCLOUD_READ_TIMEOUT_S)
        if completed.returncode != 0 and _NOT_FOUND.search(completed.stderr_text):
            return None
        if completed.returncode != 0 and _RANGE_NOT_SATISFIABLE.search(completed.stderr_text):
            return b""
        self._raise_unless_ok(completed, f"reading the tail of {uri}")
        return completed.stdout

    def download_prefix(self, source_uri: str, destination: Path) -> None:
        completed = self._rsync_down(source_uri, destination, _DOWNLOAD_TIMEOUT_S)
        self._raise_unless_ok(completed, f"downloading {source_uri} to {destination}")

    # --- the VM side: state sync, metadata server, self-delete --------------
    def sync_directory(self, source: Path, destination_uri: str, *, exclude_pattern: str) -> None:
        # No --delete-unmatched-destination-objects: a sync only ever adds or
        # updates objects, so nothing already in GCS is ever lost to it.
        argv = (
            "storage",
            "rsync",
            str(source),
            destination_uri,
            "--recursive",
            f"--exclude={exclude_pattern}",
        )
        completed = self._gcloud(argv, _SYNC_TIMEOUT_S)
        self._raise_unless_ok(completed, f"syncing {source} to {destination_uri}")

    def restore_directory(
        self, source_uri: str, destination: Path, *, exclude_pattern: str | None = None
    ) -> None:
        completed = self._rsync_down(
            source_uri, destination, _RESTORE_TIMEOUT_S, exclude_pattern=exclude_pattern
        )
        if completed.returncode != 0 and _NOTHING_TO_RESTORE.search(completed.stderr_text):
            return  # nothing was ever synced under the prefix
        self._raise_unless_ok(completed, f"restoring {source_uri} into {destination}")

    def read_instance_metadata(self, path: str) -> str | None:
        argv = (
            "curl",
            "-fsS",
            "--max-time",
            str(_METADATA_CURL_MAX_TIME_S),
            "-H",
            "Metadata-Flavor: Google",
            _METADATA_SERVER_URL + path,
        )
        try:
            completed = self._bounded(argv, _METADATA_TIMEOUT_S, program="curl")
        except BenchCloudError as exc:
            raise BenchCloudError(f"reading instance metadata {path}: {exc}") from None
        if completed.returncode != 0 and _CURL_HTTP_404.search(completed.stderr_text):
            return None
        self._raise_unless_ok(completed, f"reading instance metadata {path}")
        return completed.stdout.decode("utf-8")

    def delete_instance(self, project: str, zone: str, name: str) -> None:
        argv = ("compute", "instances", "delete", name, f"--zone={zone}", f"--project={project}")
        counting = _CountingRunner(self.runner)
        completed = self._gcloud(argv, _DELETE_TIMEOUT_S, runner=counting)
        # A retried delete whose first attempt landed answers "not found".
        retried_and_gone = counting.calls > 1 and _NOT_FOUND.search(completed.stderr_text)
        if completed.returncode == 0 or retried_and_gone:
            return
        self._raise_unless_ok(completed, f"deleting {name} in {zone}")

    def access_secret(self, project: str, secret_id: str) -> str:
        argv = (
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={secret_id}",
            f"--project={project}",
        )
        what = f"reading secret {secret_id} in {project}"
        try:
            completed = self._gcloud(argv, _SECRET_TIMEOUT_S)
        except BenchCloudError as exc:
            raise BenchCloudError(f"{what}: {exc}") from None
        # Only stderr ever reaches an error: stdout is the secret's value.
        self._raise_unless_ok(completed, what)
        # A value added with `echo … | gcloud secrets versions add --data-file=-`
        # carries echo's newline; no provider key ends in one.
        try:
            value = completed.stdout.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError:
            # The decode error would quote the value's bytes; name the shape only.
            raise BenchCloudError(
                f"{what}: got bytes that are not UTF-8, expected UTF-8 text"
            ) from None
        if not value:
            raise BenchCloudError(f"{what}: got an empty value, expected the provider key")
        return value

    # --- plumbing --------------------------------------------------------
    def _rsync_down(
        self,
        source_uri: str,
        destination: Path,
        timeout_s: float,
        *,
        exclude_pattern: str | None = None,
    ) -> CompletedCommand:
        """``gcloud storage rsync`` from a prefix into a local directory (created first)."""
        destination.mkdir(parents=True, exist_ok=True)
        # No --delete-unmatched-destination-objects: a pull or a restore only
        # adds or updates local files, so nothing kept next to them is lost.
        argv: tuple[str, ...] = ("storage", "rsync", source_uri, str(destination), "--recursive")
        if exclude_pattern is not None:
            argv += (f"--exclude={exclude_pattern}",)
        return self._gcloud(argv, timeout_s)

    def _gcloud(
        self,
        args: Sequence[str],
        timeout_s: float,
        *,
        stdin: bytes | None = None,
        runner: CommandRunner | None = None,
    ) -> CompletedCommand:
        argv = ("gcloud", *args, "--quiet")
        return self._bounded(argv, timeout_s, program="gcloud", stdin=stdin, runner=runner)

    def _bounded(
        self,
        argv: Sequence[str],
        timeout_s: float,
        *,
        program: str,
        stdin: bytes | None = None,
        runner: CommandRunner | None = None,
    ) -> CompletedCommand:
        try:
            return run_bounded(
                runner or self.runner, argv, timeout_s=timeout_s, stdin=stdin, sleep=self.sleep
            )
        except BoundedCommandError as exc:
            raise BenchCloudError(str(exc)) from None
        except OSError as exc:  # the program is not installed or not executable
            hint = "is the Cloud SDK on PATH?" if program == "gcloud" else f"is {program} on PATH?"
            raise BenchCloudError(f"running {program} failed: {exc}; {hint}") from None

    def _json_or_none(self, args: Sequence[str], what: str) -> dict[str, Any] | None:
        completed = self._gcloud(args, GCLOUD_READ_TIMEOUT_S)
        if completed.returncode != 0 and _NOT_FOUND.search(completed.stderr_text):
            return None
        self._raise_unless_ok(completed, what)
        return json_object(completed.stdout or b"{}", what)

    @staticmethod
    def _raise_unless_ok(completed: CompletedCommand, what: str) -> None:
        if completed.returncode != 0:
            raise BenchCloudError(
                f"{what} failed (exit {completed.returncode}): {completed.stderr_text}"
            )


def _listed_instance(item: Mapping[str, Any]) -> ListedInstance:
    # The API names the zone by URL: .../projects/<p>/zones/<zone>.
    return ListedInstance(
        name=str(item["name"]),
        zone=str(item.get("zone", "")).rsplit("/", 1)[-1],
        status=str(item.get("status", "")),
    )


def _labelled_instance(item: Mapping[str, Any]) -> LabelledInstance:
    listed = _listed_instance(item)
    labels = item.get("labels") or {}
    return LabelledInstance(
        name=listed.name,
        zone=listed.zone,
        status=listed.status,
        labels={str(key): str(value) for key, value in labels.items()},
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
