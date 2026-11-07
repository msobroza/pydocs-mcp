"""The cache bucket's operations of ``GcloudBenchCloud``: list a partition, upload once, mirror, download.

A mixin, so ``gcloud_bench_cloud`` keeps one adapter class while this file
holds what the persisted caches add (their directory pulls are the
adapter's own ``restore_directory``, the state restore's). Every call goes through the adapter's
``_gcloud`` (a timeout and a bounded retry on transient errors), and nothing
here ever overwrites or deletes an object: an archive upload carries the
does-not-exist precondition (``--if-generation-match=0``), and a mirror is a
``--no-clobber`` rsync with no delete flag.

An archive can be several GB; its upload and download get a timeout of their
own, longer than a state sync's.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError, ListedObject
from pydocs_eval.bench_vm.gcloud_calls import GcloudCalls
from pydocs_eval.bench_vm.gcloud_json import json_list
from pydocs_eval.bench_vm.gcloud_stderr import NO_OBJECTS_MATCHED, PRECONDITION_FAILED

_LIST_TIMEOUT_S = 120.0
ARCHIVE_TIMEOUT_S = 1800.0
# Download caches: model weights and dataset files, up to a few GB per mirror.
_MIRROR_TIMEOUT_S = 900.0
_BUCKET_OF = re.compile(r"^gs://([^/]+)/")


class GcloudCacheTransfers(GcloudCalls):
    """The cache operations of the ``BenchCloud`` port, over ``gcloud storage``."""

    __slots__ = ()

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


def _listed_objects(stdout: bytes, prefix_uri: str) -> tuple[ListedObject, ...]:
    what = f"listing {prefix_uri}"
    bucket = _BUCKET_OF.match(prefix_uri)
    answer = json_list(stdout or b"[]", what)
    if bucket is None:
        raise BenchCloudError(f"{what}: got {prefix_uri!r}, expected a gs://<bucket>/ prefix")
    try:
        listed = [
            ListedObject(uri=f"gs://{bucket.group(1)}/{item['name']}", size=int(item["size"]))
            for item in answer
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise BenchCloudError(f"{what}: got an object without a name and size ({exc})") from None
    return tuple(sorted(listed, key=lambda item: item.uri))
