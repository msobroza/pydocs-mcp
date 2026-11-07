"""One index entry as one archive: packed from a consistent copy, unpacked whole or not at all.

**Packing.** A finished entry can still be open: the retrieval runner searches
a promoted bench index entry, and the product re-runs its schema sweeps on
every open, under WAL. A plain copy of such a database can miss what is still
in its ``-wal`` file, or catch a checkpoint half way. So every SQLite file is
copied through SQLite's online backup, which reads one consistent snapshot,
and the ``-wal`` / ``-shm`` / ``-journal`` files themselves are never packed.
Every other file (the ``.tq`` vectors, the sources) is packed as it is.

**Unpacking.** The archive is extracted into a staging directory beside its
destination, then each of its top-level parts is renamed into place, unless
something is already there. A restore interrupted half way leaves only the
staging directory, never a half entry under a real name.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tarfile
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_SQLITE_HEADER = b"SQLite format 3\x00"
_SQLITE_SIDE_FILE = re.compile(r".*(-wal|-shm|-journal)$")
_STAGING_PREFIX = ".restoring-"
_STAGING_SUFFIX = ".tmp"


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    """One file of an entry: where it is read from, and its POSIX name in the archive."""

    source: Path
    name: str


def tree_members(root: Path, directory: Path) -> list[ArchiveMember]:
    """Every file under ``directory``, named relative to ``root``, without SQLite side files."""
    return [
        ArchiveMember(source=path, name=path.relative_to(root).as_posix())
        for path in sorted(directory.rglob("*"))
        if path.is_file() and not _SQLITE_SIDE_FILE.match(path.name)
    ]


def pack_archive(members: Sequence[ArchiveMember], destination: Path) -> None:
    """Write ``members`` as one uncompressed tar at ``destination``."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(dir=destination.parent) as scratch,
        tarfile.open(destination, "w") as archive,
    ):
        for index, member in enumerate(members):
            readable = _consistent_copy(member.source, Path(scratch) / f"{index}.sqlite")
            archive.add(readable, arcname=member.name, recursive=False)


def _consistent_copy(path: Path, scratch_copy: Path) -> Path:
    """``path`` itself, or a backup snapshot of it at ``scratch_copy`` when it is SQLite."""
    with path.open("rb") as handle:
        if handle.read(len(_SQLITE_HEADER)) != _SQLITE_HEADER:
            return path
    source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    target = sqlite3.connect(scratch_copy)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return scratch_copy


def unpack_archive(archive: Path, root: Path, *, part_depth: int) -> tuple[Path, ...]:
    """Extract ``archive`` under ``root``, placing each part not already there; returns them."""
    staging = root / f"{_STAGING_PREFIX}{archive.stem}{_STAGING_SUFFIX}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with tarfile.open(archive) as opened:
            names = [member.name for member in opened.getmembers()]
            # "data": no absolute paths, no escapes from the staging directory, no devices.
            opened.extractall(staging, filter="data")
        return tuple(_place(staging, root, part) for part in _parts(names, part_depth))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _parts(names: Iterable[str], depth: int) -> list[str]:
    parts = {"/".join(PurePosixPath(name).parts[:depth]) for name in names}
    return sorted(part for part in parts if part.count("/") == depth - 1)


def _place(staging: Path, root: Path, part: str) -> Path:
    target = root / part
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        (staging / part).rename(target)
    return target
