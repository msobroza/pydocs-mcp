"""The finished index entries a run has to persist, found from each producer's own completion marker.

- **Bench index cache** (``_bench_cache``): an entry is built under a
  temporary ``<key>.<pid>.tmp`` name and renamed to its 64-hex key only once
  built. The rename is the marker: a key-named directory is finished, anything
  else is a build in progress.
- **Before/after corpus** (``campaign.before_after_bundle_marker``): a bundle
  is finished once it carries its marker, written after the copy. Its archive
  holds the corpus source, the marker, and the bundle's index files taken from
  the canonical build the marker names, which no serve ever opened.

Each entry's archive members are named relative to its producer's root, and
its parts sit ``part_depth`` levels below it: the ``<key>`` directory, or the
``sources/<slug>`` and ``bundles/<bundle>`` directories.
"""

from __future__ import annotations

from collections.abc import Set
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydocs_eval._bench_cache import ENTRY_KEY_PATTERN
from pydocs_eval.bench_vm.cache_archives import ArchiveMember, tree_members
from pydocs_eval.bench_vm.cache_layout import ArchiveShelf
from pydocs_eval.campaign.before_after_bundle_marker import (
    BUNDLE_ASSEMBLED_MARKER_NAME,
    AssembledBundle,
    read_bundle_assembled_marker,
)

PART_DEPTH = {ArchiveShelf.BENCH_INDEX: 1, ArchiveShelf.BEFORE_AFTER: 2}


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """One finished entry: its name (the archive's stem) and the files it is packed from."""

    name: str
    members: tuple[ArchiveMember, ...]


def finished_entries(
    shelf: ArchiveShelf, root: Path, *, skip_names: Set[str] = frozenset()
) -> tuple[CacheEntry, ...]:
    """The finished entries under ``root`` (the producer's root for ``shelf``), less ``skip_names``.

    An entry in ``skip_names`` (already in the bucket) is never walked.
    Raises ``OSError`` when an entry moves or vanishes while it is walked.
    """
    if not root.is_dir():
        return ()
    if shelf is ArchiveShelf.BENCH_INDEX:
        return _promoted_bench_index_entries(root, skip_names)
    return _assembled_before_after_bundles(root, skip_names)


def _promoted_bench_index_entries(root: Path, skip_names: Set[str]) -> tuple[CacheEntry, ...]:
    return tuple(
        CacheEntry(name=entry.name, members=tuple(tree_members(root, entry)))
        for entry in sorted(root.iterdir())
        if entry.name not in skip_names
        and entry.is_dir()
        and ENTRY_KEY_PATTERN.fullmatch(entry.name)
    )


def _assembled_before_after_bundles(root: Path, skip_names: Set[str]) -> tuple[CacheEntry, ...]:
    entries = []
    for marker_path in sorted(root.glob(f"*/*/{BUNDLE_ASSEMBLED_MARKER_NAME}")):
        bundle = marker_path.parent
        if bundle.name in skip_names:
            continue
        assembled = read_bundle_assembled_marker(bundle)
        members = None if assembled is None else _bundle_members(root, bundle, assembled)
        if members is not None:
            entries.append(CacheEntry(name=bundle.name, members=members))
    return tuple(entries)


def _bundle_members(
    root: Path, bundle: Path, assembled: AssembledBundle
) -> tuple[ArchiveMember, ...] | None:
    """The bundle's archive members, ``None`` when the marker names anything not there."""
    source = _inside(root, assembled.source_dir)
    canonical = [_inside(root, name) for name in assembled.index_files]
    if (
        source is None
        or not source.is_dir()
        or any(p is None or not p.is_file() for p in canonical)
    ):
        return None
    bundle_name = bundle.relative_to(root).as_posix()
    marker = bundle / BUNDLE_ASSEMBLED_MARKER_NAME
    return (
        *tree_members(root, source),
        ArchiveMember(source=marker, name=f"{bundle_name}/{marker.name}"),
        *(
            ArchiveMember(source=path, name=f"{bundle_name}/{path.name}")
            for path in canonical
            if path is not None
        ),
    )


def _inside(root: Path, relative: str) -> Path | None:
    """``root / relative`` when ``relative`` stays under ``root``; ``None`` otherwise."""
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        return None
    return root.joinpath(*pure.parts)
