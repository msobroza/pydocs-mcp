"""The cache bucket's operations of ``GcloudBenchCloud``: list a partition, upload once, mirror, restore.

A mixin, so ``gcloud_bench_cloud`` keeps one adapter class while this file
holds what the persisted caches add. Every call goes through the adapter's
``_gcloud`` (a timeout and a bounded retry on transient errors), and nothing
here ever overwrites or deletes an object: an archive upload carries the
does-not-exist precondition (``--if-generation-match=0``), and a mirror is a
``--no-clobber`` rsync with no delete flag.

An archive can be several GB; its upload and download get a timeout of their
own, longer than a state sync's.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError, ListedObject
from pydocs_eval.bench_vm.bounded_command import CommandRunner, CompletedCommand
from pydocs_eval.bench_vm.gcloud_stderr import NO_OBJECTS_MATCHED, PRECONDITION_FAILED

_LIST_TIMEOUT_S = 120.0
ARCHIVE_TIMEOUT_S = 1800.0
# Download caches: model weights and dataset files, up to a few GB per mirror.
_MIRROR_TIMEOUT_S = 900.0
_BUCKET_OF = re.compile(r"^gs://([^/]+)/")


class GcloudCacheTransfers:
    """The cache operations of the ``BenchCloud`` port, over ``gcloud storage``."""

    __slots__ = ()

    def _gcloud(
        self,
        args: Sequence[str],
        timeout_s: float,
        *,
        stdin: bytes | None = None,
        runner: CommandRunner | None = None,
    ) -> CompletedCommand:
        raise NotImplementedError  # GcloudBenchCloud provides it

    @staticmethod
    def _raise_unless_ok(completed: CompletedCommand, what: str) -> None:
        raise NotImplementedError  # GcloudBenchCloud provides it

    def list_objects(self, prefix_uri: str) -> tuple[ListedObject, ...]:
        argv = ("storage", "objects", "list", prefix_uri + "**", "--format=json")
        completed = self._gcloud(argv, _LIST_TIMEOUT_S)
        if completed.returncode != 0 and NO_OBJECTS_MATCHED.search(completed.stderr_text):
            return ()
        self._raise_unless_ok(completed, f"listing {prefix_uri}")
        return _listed_objects(completed.stdout, prefix_uri)

    def upload_object_if_absent(self, source: Path, uri: str) -> bool:
        argv = ("storage", "cp", str(source), uri, "--if-generation-match=0")
        completed = self._gcloud(argv, ARCHIVE_TIMEOUT_S)
        if completed.returncode != 0 and PRECONDITION_FAILED.search(completed.stderr_text):
            # Already there: another run (or an earlier attempt of this
            # upload whose answer was lost) put the entry; it is left as is.
            return False
        self._raise_unless_ok(completed, f"uploading {source} to {uri}")
        return True

    def download_object(self, uri: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        completed = self._gcloud(("storage", "cp", uri, str(destination)), ARCHIVE_TIMEOUT_S)
        self._raise_unless_ok(completed, f"downloading {uri} to {destination}")

    def upload_directory_without_overwrite(
        self, source: Path, destination_uri: str, *, exclude_pattern: str
    ) -> None:
        argv = (
            "storage",
            "rsync",
            str(source),
            destination_uri,
            "--recursive",
            "--no-clobber",
            f"--exclude={exclude_pattern}",
        )
        completed = self._gcloud(argv, _MIRROR_TIMEOUT_S)
        self._raise_unless_ok(completed, f"mirroring {source} to {destination_uri}")

    def restore_directory(self, source_uri: str, destination: Path) -> None:
        # rsync needs its destination to exist; no delete flag, so nothing
        # already in the directory is lost.
        destination.mkdir(parents=True, exist_ok=True)
        argv = ("storage", "rsync", source_uri, str(destination), "--recursive")
        completed = self._gcloud(argv, _MIRROR_TIMEOUT_S)
        if completed.returncode != 0 and NO_OBJECTS_MATCHED.search(completed.stderr_text):
            return
        self._raise_unless_ok(completed, f"restoring {source_uri} to {destination}")


def _listed_objects(stdout: bytes, prefix_uri: str) -> tuple[ListedObject, ...]:
    what = f"listing {prefix_uri}"
    bucket = _BUCKET_OF.match(prefix_uri)
    try:
        answer = json.loads(stdout or b"[]")
    except json.JSONDecodeError as exc:
        raise BenchCloudError(f"{what}: got unreadable JSON ({exc}), expected a list") from None
    if bucket is None or not isinstance(answer, list):
        raise BenchCloudError(f"{what}: got {answer!r}, expected a JSON list of objects")
    try:
        listed = [
            ListedObject(uri=f"gs://{bucket.group(1)}/{item['name']}", size=int(item["size"]))
            for item in answer
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise BenchCloudError(f"{what}: got an object without a name and size ({exc})") from None
    return tuple(sorted(listed, key=lambda item: item.uri))
