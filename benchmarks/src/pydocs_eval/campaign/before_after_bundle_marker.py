"""The completion marker of one before/after corpus bundle: written last, after the bundle is assembled.

``_assemble_bundle`` copies a canonical ``.db``/``.tq`` pair into the bundle
directory one file at a time, so a bundle directory alone cannot say whether
it is finished. The marker can: ``build_missing_workspaces`` writes it,
atomically, only after the copy returned. A bundle without it is in progress
(or was interrupted) and is never persisted.

It also names what the bundle was assembled FROM, relative to the
task-workspaces root: the corpus source directory (the serve tools read files
from it) and the canonical index files. Those canonical files are never
served, so their bytes stay exactly as built, while a serve child opens the
bundle's own copy read-write (``index_cache.preseed_workspace``). The bench VM
supervisor reads the marker to pack the entry from those unserved bytes.

The marker is a dot-file, so ``serve --workspace``'s non-recursive ``*.db``
glob over the bundle directory never sees it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.atomic_text import write_text_atomically

BUNDLE_ASSEMBLED_MARKER_NAME = ".bundle-assembled.json"
_SOURCE_KEY = "source_dir"
_INDEX_FILES_KEY = "index_files"


@dataclass(frozen=True, slots=True)
class AssembledBundle:
    """What a finished bundle was assembled from, as POSIX paths relative to the task-workspaces root."""

    source_dir: str
    index_files: tuple[str, ...]


def write_bundle_assembled_marker(bundle_dir: Path, assembled: AssembledBundle) -> None:
    """Mark ``bundle_dir`` finished; a reader sees no marker or the whole one."""
    payload = {_SOURCE_KEY: assembled.source_dir, _INDEX_FILES_KEY: list(assembled.index_files)}
    write_text_atomically(bundle_dir / BUNDLE_ASSEMBLED_MARKER_NAME, json.dumps(payload) + "\n")


def read_bundle_assembled_marker(bundle_dir: Path) -> AssembledBundle | None:
    """The marker of ``bundle_dir``; ``None`` when it is absent or unreadable (not finished)."""
    try:
        payload = json.loads((bundle_dir / BUNDLE_ASSEMBLED_MARKER_NAME).read_text("utf-8"))
        source_dir = payload[_SOURCE_KEY]
        index_files = tuple(payload[_INDEX_FILES_KEY])
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not isinstance(source_dir, str) or not all(isinstance(f, str) for f in index_files):
        return None
    return AssembledBundle(source_dir=source_dir, index_files=index_files)


__all__ = (
    "BUNDLE_ASSEMBLED_MARKER_NAME",
    "AssembledBundle",
    "read_bundle_assembled_marker",
    "write_bundle_assembled_marker",
)
