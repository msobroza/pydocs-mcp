"""The run's persisted caches: restored at boot, pushed at every sync, never overwritten or deleted in GCS.

**Restore**, before the child starts: the shared partition and the run's
dataset partition (``cache_layout``) are listed once. Each download cache with
objects is pulled into its fixed path; each index archive on one of the run's
shelves is downloaded and unpacked whole. The listed bytes of what was pulled
are the record's ``cache_bytes_restored``.

**Push**, at each sync: every finished entry not yet in the bucket is packed
as one archive and uploaded under the does-not-exist precondition, so an
object already there (another run's entry) is never replaced. Download caches
mirror their new files without replacing any. The bytes of what landed are the
record's ``cache_bytes_pushed``.

**Which shelves a run has.** Before/after pushes and restores its corpora. A
retrieval run's bench index entries are persisted only when the spec has a
``corpus_dir``: the runner then indexes that one directory for every task, so
every entry is the reference project's. Without it, every shipped dataset
materializes each task's corpus into a fresh temporary directory, and the
entries, keyed on that path, can never be hit again on any machine.

**When a transfer may start.** A transfer can take a long time (an archive
is several GB, and each call retries), so the caller passes a gate asked
before each transfer STARTS (``supervisor_caches``: the restore within its
share of the time to the soft deadline, a push until a shutdown is
requested); what it refuses is deferred (logged
``bench_vm_cache_transfer_deferred``) to the next push.

A cache is advisory: every failure is logged (``bench_vm_cache_restore_failed``,
``bench_vm_cache_push_failed``) and the run carries on.
"""

from __future__ import annotations

import re
import sqlite3
import tarfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, BenchCloudError, ListedObject
from pydocs_eval.bench_vm.cache_archives import pack_archive, unpack_archive
from pydocs_eval.bench_vm.cache_entries import PART_DEPTH, CacheEntry, finished_entries
from pydocs_eval.bench_vm.cache_layout import (
    CACHE_MIRROR_EXCLUDE_PATTERN,
    ArchiveShelf,
    CacheLayout,
    CacheMirror,
    deps_partition_name,
)
from pydocs_eval.bench_vm.container_paths import RunDirectories
from pydocs_eval.bench_vm.launch_plan import image_flavour
from pydocs_eval.bench_vm.run_kinds import RunKind
from pydocs_eval.bench_vm.supervised_run import SupervisedRun

ARCHIVE_SUFFIX = ".tar"
# Beside the caches, never inside a mirrored directory or a producer's root.
_STAGING_DIR_NAME = ".cache-staging"
_CACHE_ERRORS = (BenchCloudError, OSError, tarfile.TarError, sqlite3.Error)

CacheLog = Callable[..., None]
TransferGate = Callable[[], bool]


def _always() -> bool:
    return True


@dataclass
class PersistedCaches:
    """One run's caches against the cache bucket; ``bytes_*`` count this attempt's transfers."""

    cloud: BenchCloud
    layout: CacheLayout
    shelves: tuple[ArchiveShelf, ...]
    run_id: str
    log: CacheLog
    bytes_restored: int = 0
    bytes_pushed: int = 0
    # The earlier attempts' counts, as the run record held them at boot.
    earlier_bytes_restored: int = 0
    earlier_bytes_pushed: int = 0
    # What is known to be in the bucket: archive URIs, and each mirror's files.
    _archives_in_bucket: set[str] = field(default_factory=set)
    _mirrored: dict[str, set[str]] = field(default_factory=dict)
    # Asked before each transfer starts, and how many this call deferred.
    _may_start_transfer: TransferGate = _always
    _deferred: int = 0

    @classmethod
    def for_run(
        cls, cloud: BenchCloud, run: SupervisedRun, dirs: RunDirectories, log: CacheLog
    ) -> PersistedCaches:
        spec = run.spec
        layout = CacheLayout(
            cache_dir=dirs.cache_dir,
            bucket=run.facts.cache_bucket,
            dataset=spec.dataset,
            deps_partition=deps_partition_name(dirs.repo_dir, str(image_flavour(spec))),
        )
        shelves: tuple[ArchiveShelf, ...] = ()
        if spec.kind is RunKind.BEFORE_AFTER:
            shelves = (ArchiveShelf.BEFORE_AFTER,)
        elif spec.kind is RunKind.RETRIEVAL and spec.corpus_dir is not None:
            shelves = (ArchiveShelf.BENCH_INDEX,)
        return cls(
            cloud=cloud,
            layout=layout,
            shelves=shelves,
            run_id=run.facts.run_id,
            log=log,
            earlier_bytes_restored=run.record.cache_bytes_restored,
            earlier_bytes_pushed=run.record.cache_bytes_pushed,
        )

    def record_counts(self) -> dict[str, object]:
        """The run record's cache byte fields: every attempt's transfers so far."""
        return {
            "cache_bytes_restored": self.earlier_bytes_restored + self.bytes_restored,
            "cache_bytes_pushed": self.earlier_bytes_pushed + self.bytes_pushed,
        }

    # --- restore -----------------------------------------------------------------
    def restore(self, *, may_start_transfer: TransferGate = _always) -> int:
        """Pull the run's partitions into the fixed paths; returns the bytes restored."""
        self._begin(may_start_transfer)
        listings = [self._list(self.layout.shared_uri), self._list(self.layout.dataset_uri)]
        listed = tuple(o for listing in listings if listing is not None for o in listing)
        for mirror in self.layout.mirrors():
            objects = [o for o in listed if o.uri.startswith(mirror.uri)]
            if None not in listings:  # what is in the bucket is known, pulled or not
                self._mirrored[mirror.uri] = {o.uri.removeprefix(mirror.uri) for o in objects}
            self._restore_mirror(mirror, objects)
        for shelf in self.shelves:
            for archive in self._shelf_archives(shelf, listed):
                self._restore_archive(shelf, archive)
        self._end("restore")
        return self.bytes_restored

    def _list(self, prefix_uri: str) -> tuple[ListedObject, ...] | None:
        try:
            return self.cloud.list_objects(prefix_uri)
        except BenchCloudError as exc:
            self._failed("bench_vm_cache_restore_failed", prefix_uri, exc)
            return None

    def _restore_mirror(self, mirror: CacheMirror, objects: list[ListedObject]) -> None:
        if not objects or not self._may_start():
            return
        try:
            self.cloud.restore_directory(mirror.uri, mirror.local_dir)
        except _CACHE_ERRORS as exc:
            self._failed("bench_vm_cache_restore_failed", mirror.uri, exc)
            return
        self._restored(mirror.uri, sum(o.size for o in objects))

    def _shelf_archives(
        self, shelf: ArchiveShelf, listed: tuple[ListedObject, ...]
    ) -> list[ListedObject]:
        prefix = self.layout.shelf_uri(shelf)
        if prefix is None:
            return []
        pattern = re.compile(re.escape(prefix) + r"[^/]+" + re.escape(ARCHIVE_SUFFIX))
        return [o for o in listed if pattern.fullmatch(o.uri)]

    def _restore_archive(self, shelf: ArchiveShelf, archive: ListedObject) -> None:
        self._archives_in_bucket.add(archive.uri)
        if not self._may_start():
            return
        staged = self._staging_dir / archive.uri.rsplit("/", 1)[-1]
        try:
            self.cloud.download_object(archive.uri, staged)
            unpack_archive(staged, self._root_of(shelf), part_depth=PART_DEPTH[shelf])
        except _CACHE_ERRORS as exc:
            self._failed("bench_vm_cache_restore_failed", archive.uri, exc)
            return
        finally:
            staged.unlink(missing_ok=True)
        self._restored(archive.uri, archive.size)

    def _restored(self, uri: str, size: int) -> None:
        self.bytes_restored += size
        self.log("bench_vm_cache_restored", run_id=self.run_id, source=uri, bytes=size)

    # --- push --------------------------------------------------------------------
    def push(self, *, may_start_transfer: TransferGate = _always) -> int:
        """Push what finished since the last push; returns the bytes pushed by this call."""
        self._begin(may_start_transfer)
        before = self.bytes_pushed
        for shelf in self.shelves:
            self._push_shelf(shelf)
        for mirror in self.layout.mirrors():
            self._push_mirror(mirror)
        self._end("push")
        return self.bytes_pushed - before

    def _push_shelf(self, shelf: ArchiveShelf) -> None:
        prefix = self.layout.shelf_uri(shelf)
        if prefix is None:  # no uv.lock in the image: no partition to push to
            return
        known = {
            u.removeprefix(prefix).removesuffix(ARCHIVE_SUFFIX) for u in self._archives_in_bucket
        }
        try:
            entries = finished_entries(shelf, self._root_of(shelf), skip_names=frozenset(known))
        except OSError as exc:  # an entry moved or vanished while it was walked
            self._failed("bench_vm_cache_push_failed", prefix, exc)
            return
        for entry in entries:
            self._push_archive(prefix + entry.name + ARCHIVE_SUFFIX, entry)

    def _push_archive(self, uri: str, entry: CacheEntry) -> None:
        if not self._may_start():
            return
        packed = self._staging_dir / f"{entry.name}{ARCHIVE_SUFFIX}"
        try:
            pack_archive(entry.members, packed)
            size = packed.stat().st_size
            uploaded = self.cloud.upload_object_if_absent(packed, uri)
        except _CACHE_ERRORS as exc:
            self._failed("bench_vm_cache_push_failed", uri, exc)
            return
        finally:
            packed.unlink(missing_ok=True)
        self._archives_in_bucket.add(uri)
        self._pushed(uri, size if uploaded else 0, already_there=not uploaded)

    def _push_mirror(self, mirror: CacheMirror) -> None:
        try:
            local = _mirrorable_files(mirror.local_dir)
        except OSError as exc:  # a file moved or vanished while it was walked
            self._failed("bench_vm_cache_push_failed", mirror.uri, exc)
            return
        known = self._mirror_files_in_bucket(mirror) if local else None
        if known is None:
            return
        new = set(local) - known
        if not new or not self._may_start():
            return
        try:
            self.cloud.upload_directory_without_overwrite(
                mirror.local_dir, mirror.uri, exclude_pattern=CACHE_MIRROR_EXCLUDE_PATTERN
            )
        except BenchCloudError as exc:
            self._failed("bench_vm_cache_push_failed", mirror.uri, exc)
            return
        # Files written during the upload are not in `local`: the next push
        # counts them, and the no-clobber mirror skips any already there.
        known.update(new)
        self._pushed(mirror.uri, sum(local[name] for name in new), already_there=False)

    def _mirror_files_in_bucket(self, mirror: CacheMirror) -> set[str] | None:
        """The mirror's files already in the bucket; ``None`` when they cannot be listed.

        Known from the restore's listing, whether or not it pulled the mirror.
        When that listing failed, the bucket is listed once here: the
        no-clobber upload skips a file already there, so counting it as pushed
        would inflate ``cache_bytes_pushed`` on every run that downloads it again.
        """
        known = self._mirrored.get(mirror.uri)
        if known is not None:
            return known
        try:
            listed = self.cloud.list_objects(mirror.uri)
        except BenchCloudError as exc:
            self._failed("bench_vm_cache_push_failed", mirror.uri, exc)
            return None
        known = {o.uri.removeprefix(mirror.uri) for o in listed}
        self._mirrored[mirror.uri] = known
        return known

    def _pushed(self, uri: str, size: int, *, already_there: bool) -> None:
        self.bytes_pushed += size
        self.log(
            "bench_vm_cache_pushed",
            run_id=self.run_id,
            destination=uri,
            bytes=size,
            already_there=already_there,
        )

    # --- plumbing ----------------------------------------------------------------
    def _begin(self, may_start_transfer: TransferGate) -> None:
        self._may_start_transfer, self._deferred = may_start_transfer, 0

    def _may_start(self) -> bool:
        if self._may_start_transfer():
            return True
        self._deferred += 1
        return False

    def _end(self, step: str) -> None:
        if self._deferred:
            self.log(
                "bench_vm_cache_transfer_deferred",
                run_id=self.run_id,
                step=step,
                transfers=self._deferred,
                detail="the restore's share of the deadline is spent, or a shutdown is requested",
            )

    @property
    def _staging_dir(self) -> Path:
        return self.layout.cache_dir / _STAGING_DIR_NAME

    def _root_of(self, shelf: ArchiveShelf) -> Path:
        if shelf is ArchiveShelf.BENCH_INDEX:
            return self.layout.bench_index_root
        return self.layout.task_workspaces_root

    def _failed(self, event: str, uri: str, exc: Exception) -> None:
        detail = f"{type(exc).__name__}: {exc}"
        self.log(event, run_id=self.run_id, uri=uri, detail=detail)


def _mirrorable_files(directory: Path) -> dict[str, int]:
    """Each file a mirror would upload, by its directory-relative POSIX path, with its size."""
    if not directory.is_dir():
        return {}
    files = {}
    for path in directory.rglob("*"):
        relative = path.relative_to(directory).as_posix()
        if path.is_file() and not re.match(CACHE_MIRROR_EXCLUDE_PATTERN, relative):
            files[relative] = path.stat().st_size
    return files
