"""Materialize a ref's blobs into a scratch tree (spec §6.3 step 3, #309).

The ingestion stages, the Rust/Python file readers and the module-id rule all
work on real files under a root. Writing the blobs of a ref that is not checked
out into a scratch tree with the project's relative layout keeps every one of
them identical to the working-tree pass: same ``source_path`` values, same
module ids, same text. The blob bytes come only through
``GitRepository.read_blobs``; nothing here reads ``.git`` or spawns git.

Caller contract: the ``scratch_tree`` block must not exit while a
``materialize_blobs`` call on its root is still running (a worker thread under
a cancelled task). Such a call fails at its next write instead of rebuilding
the removed tree, but files it wrote while ``rmtree`` was still walking the
tree can outlive it (logged as ``blob_scratch_cleanup_failed``).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydocs_mcp.application.protocols import GitRepository

log = logging.getLogger("pydocs-mcp")

_SCRATCH_PREFIX = "pydocs-mcp-blobs-"
# The name the stand-in takes when the project root has none (a filesystem root).
_UNNAMED_ROOT = "project"
# ``.git`` and the names NTFS resolves to it once Win32 strips trailing dots and
# spaces (``GIT~1`` is its 8.3 short name) — the set git's own protectNTFS
# refuses on every platform.
_GIT_DIR_ALIASES = frozenset({".git", "git~1"})
_NTFS_STRIPPED_TAIL = ". "
# Characters Windows reads as path structure, not as a name: a directory
# separator, and a drive or alternate-data-stream marker. git for Windows
# refuses to check them out; POSIX git keeps them as plain name bytes, so they
# are refused only where they would change where a blob lands.
_WINDOWS_STRUCTURE_CHARS = ("\\", ":")
_ON_WINDOWS = sys.platform == "win32"


@contextmanager
def scratch_tree(*, stand_in_for: Path) -> Iterator[Path]:
    """An empty stand-in for the project root ``stand_in_for``, in the system temp dir.

    WHY the system temp dir, never the repository or a worktree (#309): git
    finds a repository by walking up from a directory, so a git command run in
    a scratch tree inside the checkout would read the enclosing repository,
    and the file watcher and a working-tree walk would see every copy. A temp
    dir that resolves inside the project (TMPDIR pointed there) is refused.

    WHY the stand-in carries the project root's name, and why the argument is
    keyword-only: a root holding ``__init__.py`` is itself the top package, so
    its directory name is the first segment of every module id
    (``python_module_id``); a stale positional call passing the cache dir must
    fail loudly instead of renaming every module.

    The holder directory is removed whether the pass succeeded or failed.
    """
    holder = Path(tempfile.mkdtemp(prefix=_SCRATCH_PREFIX))
    try:
        _refuse_holder_inside_project(holder, stand_in_for)
        root = holder / (Path(os.path.abspath(stand_in_for)).name or _UNNAMED_ROOT)  # noqa: PTH100
        root.mkdir()
        yield root
    finally:
        _remove_scratch(holder)


def materialize_blobs(
    git: GitRepository, entries: Sequence[tuple[str, str]], root: Path
) -> tuple[str, ...]:
    """Write ``(blob_sha, path)`` pairs under ``root``; return the paths written.

    ONE ``read_blobs`` call (one ``cat-file --batch`` process) whatever the
    count. Every path is checked before git is asked for anything, so a
    refused entry leaves the scratch tree empty. ``root`` itself is never
    created: a removed scratch tree raises ``FileNotFoundError``.
    """
    targets = {path: _scratch_target(root, path) for _, path in entries}
    if not root.is_dir():
        raise FileNotFoundError(f"scratch root is gone: got {root}, expected a live scratch_tree")
    written: list[str] = []
    for path, text in git.read_blobs(entries):
        _write_blob_text(root, targets[path], text)
        written.append(path)
    return tuple(written)


def _scratch_target(root: Path, path: str) -> Path:
    """``root / path`` for a project-relative POSIX path; ``ValueError`` for anything else.

    A crafted tree can carry ``..`` or ``.git`` entries (fsck is off by default
    on fetch): the first would write outside the scratch tree, the second would
    plant a repository — config and hooks included — that a git command run in
    the scratch tree would honor.
    """
    posix = PurePosixPath(path)
    if _is_unsafe_blob_path(path, posix) or not _stays_under(root, path):
        raise ValueError(
            f"invalid blob path: got {path!r}, expected a project-relative POSIX path "
            "without '..' or '.git' segments"
        )
    return root.joinpath(*posix.parts)


def _is_unsafe_blob_path(path: str, posix: PurePosixPath) -> bool:
    """Empty, absolute, a ``..`` or ``.git``-alias segment, or Windows path structure."""
    return (
        not posix.parts
        or posix.is_absolute()
        or any(part == ".." or _aliases_git_dir(part) for part in posix.parts)
        or (_ON_WINDOWS and any(char in path for char in _WINDOWS_STRUCTURE_CHARS))
    )


def _aliases_git_dir(part: str) -> bool:
    """True for ``.git`` in any case, and for every NTFS alias of it (git's is_ntfs_dotgit)."""
    return part.rstrip(_NTFS_STRIPPED_TAIL).casefold() in _GIT_DIR_ALIASES


def _stays_under(root: Path, path: str) -> bool:
    """True when ``root / path`` normalizes to a location strictly inside ``root``.

    The last net behind the segment checks, in the host OS's own path rules:
    whatever a future edit to those checks lets through still cannot leave the
    scratch tree.
    """
    base = os.path.abspath(root)  # noqa: PTH100
    target = os.path.abspath(os.path.join(base, path))  # noqa: PTH100, PTH118
    try:
        return target != base and os.path.commonpath([base, target]) == base
    except ValueError:  # Windows: the two paths sit on different drives
        return False


def _refuse_holder_inside_project(holder: Path, project_root: Path) -> None:
    """``ValueError`` when the temp dir ``mkdtemp`` picked resolves inside the project.

    ``mkdtemp`` follows TMPDIR/TEMP/TMP, and a TMPDIR inside the checkout
    (direnv, sandboxed agents, some CI) would break the rule ``scratch_tree``
    exists to keep (#309 review).
    """
    project = Path(project_root).resolve()
    if holder.resolve().is_relative_to(project):
        raise ValueError(
            f"scratch tree inside the project: got temp dir {holder.parent} under {project}, "
            "expected a temp dir outside it (point TMPDIR elsewhere)"
        )


def _write_blob_text(root: Path, target: Path, text: str) -> None:
    """Write ``text`` as UTF-8 with no newline translation.

    ``read_blobs`` decodes with replacement, and the readers decode files the
    same way, so a non-UTF-8 blob reads back as the same text the working-tree
    file gives. ``newline=""`` keeps ``\\r\\n`` and lone ``\\n`` exactly as
    committed on every OS (text mode would write ``os.linesep``).
    """
    _make_dirs_below(root, target.parent)
    target.write_text(text, encoding="utf-8", newline="")


def _make_dirs_below(root: Path, directory: Path) -> None:
    """Create each missing directory from ``root`` down to ``directory``, never ``root``.

    WHY no ``mkdir(parents=True)`` (#309 review): a materialization still
    running after a cancelled pass left ``scratch_tree`` would rebuild the
    removed holder — with umask permissions instead of mkdtemp's 0o700, in the
    shared temp dir — and fill it with the branch's source. One level at a
    time, a vanished root raises ``FileNotFoundError`` instead.
    """
    current = root
    for part in directory.relative_to(root).parts:
        current = current / part
        current.mkdir(exist_ok=True)


def _remove_scratch(holder: Path) -> None:
    """Delete the holder; a failure is logged, never raised.

    A leftover temp directory is a leak, not a wrong answer, and raising here
    would replace the exception that ended a failed pass.
    """
    try:
        shutil.rmtree(holder)
    except OSError as exc:
        payload = {"event": "blob_scratch_cleanup_failed", "path": str(holder), "error": str(exc)}
        log.warning(json.dumps(payload))


__all__ = ("materialize_blobs", "scratch_tree")
