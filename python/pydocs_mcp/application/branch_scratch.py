"""The scratch tree of one git-objects branch pass (spec §6.3 steps 2-3, #310).

Four concerns the branch indexer delegates here: making and removing the
scratch tree off the event loop, writing blobs into it so a cancelled pass
cannot outlive it, which blobs lay out the packages (module ids depend on
them), and which cache hits still name the module this branch's layout gives
them.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import TypeVar

from pydocs_mcp.application.extraction_cache import CachedFile
from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.extraction.strategies.python_module_id import package_rooted_module_id
from pydocs_mcp.git.blob_scratch import materialize_blobs, scratch_tree
from pydocs_mcp.storage.branch_records import BranchFile

_Result = TypeVar("_Result")
_PACKAGE_MARKER = "__init__.py"
_PYTHON_SUFFIX = ".py"
# The root file ``[tool.pydocs-mcp] exclude_dirs`` is read from (#309).
ROOT_PYPROJECT_PATH = "pyproject.toml"


@contextlib.asynccontextmanager
async def scratch_tree_off_loop(*, stand_in_for: Path) -> AsyncIterator[Path]:
    """``scratch_tree`` entered and left in worker threads (#310 review).

    The exit walks every materialized blob (``rmtree``), and Task 18's
    ref-driven refresh runs these passes inside a live ``serve``, so neither
    end may block the event loop. Both hops run to their end even when the
    pass is cancelled: a cancelled enter would otherwise leak the temp dir it
    was making, and a cancelled exit would return before the tree is gone.
    """
    stack = contextlib.ExitStack()
    try:
        yield await _run_to_end(stack.enter_context, scratch_tree(stand_in_for=stand_in_for))
    finally:
        await _run_to_end(stack.close)


async def materialize_blobs_in_scratch(
    git: GitRepository, files: Iterable[BranchFile], root: Path
) -> None:
    """Write ``files``' blobs under ``root`` off the loop, one ``read_blobs`` call.

    WHY awaited to its end even when cancelled (#309 obligation): a cancelled
    ``to_thread`` returns at once while its thread keeps writing the branch's
    source. The ``scratch_tree`` block around this call would then remove the
    tree under a live writer, and files written after the removal would leak
    into the shared temp dir. Run to its end, the writer finishes first; the
    cancellation is re-raised after it, and the block's exit removes it all.
    """
    entries = tuple({f.path: (f.blob_sha, f.path) for f in files}.values())
    if entries:
        await _run_to_end(materialize_blobs, git, entries, root)


async def _run_to_end(func: Callable[..., _Result], /, *args: object) -> _Result:
    """``func(*args)`` in a worker thread, awaited to its end even when cancelled;
    the cancellation is re-raised once the thread is done."""
    worker = asyncio.ensure_future(asyncio.to_thread(func, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await _wait_out_worker(worker)
        raise


async def _wait_out_worker(worker: asyncio.Future[_Result]) -> None:
    """Wait for ``worker`` through any further cancellation; its result is moot."""
    while not worker.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait({worker})
    if not worker.cancelled():
        # Retrieved so asyncio never logs it: the pass ends cancelled either way.
        worker.exception()


def package_marker_files(files: Iterable[BranchFile]) -> tuple[BranchFile, ...]:
    """The manifest's ``__init__.py`` files — hits and empty ones included.

    WHY every one (#309 obligation): ``package_rooted_module_id`` re-roots a
    file at its topmost package, which it finds by the markers on disk, so the
    scratch tree needs them all for the misses to get this branch's ids.
    """
    return tuple(f for f in files if PurePosixPath(f.path).name == _PACKAGE_MARKER)


def root_pyproject_file(listing: Iterable[tuple[str, str, int]], branch: str) -> BranchFile | None:
    """The ref's root ``pyproject.toml``, whatever the discovery scope admits.

    Explicit-path discovery reads its excludes from the scratch root (#309).
    """
    for path, blob, _size in listing:
        if path == ROOT_PYPROJECT_PATH:
            return BranchFile(branch, path, blob)
    return None


def python_module_ids(root: Path, files: Iterable[BranchFile]) -> dict[str, str]:
    """``{path: module id}`` of every Python file of the manifest, over the
    scratch layout — through ``package_rooted_module_id``, the rule's one home."""
    return {
        f.path: package_rooted_module_id(str(root.joinpath(*PurePosixPath(f.path).parts)), root)
        for f in files
        if PurePosixPath(f.path).suffix.lower() == _PYTHON_SUFFIX
    }


def split_hits_by_module_id(
    hits: Sequence[tuple[BranchFile, CachedFile]], module_ids: Mapping[str, str]
) -> tuple[tuple[CachedFile, ...], tuple[BranchFile, ...]]:
    """``(kept, demoted)``: a Python hit is reused only if its cached tree names
    the module this branch gives the file, and no other file shares that id.

    A row is keyed by the blob and the path, but the module id also depends on
    the ``__init__.py`` files around it: a branch that adds or drops one moves
    the id (``pkg.a`` becomes ``src.pkg.a``), and a reused tree would then
    answer under the old name. A shared id (two files, one module) makes both
    misses, as the working-tree pass leaves them uncached; a hit kept beside a
    colliding miss would share chunk hashes the embed budget counts once.
    Non-Python module ids are path-derived, so those hits always hold.
    """
    shared = {module for module, count in Counter(module_ids.values()).items() if count > 1}
    kept: list[CachedFile] = []
    demoted: list[BranchFile] = []
    for file, cached in hits:
        if _module_id_holds(cached, module_ids.get(file.path), shared):
            kept.append(cached)
        else:
            demoted.append(file)
    return tuple(kept), tuple(demoted)


def _module_id_holds(cached: CachedFile, module_id: str | None, shared: set[str]) -> bool:
    if module_id is None:
        return True
    tree = cached.artifacts.tree
    return tree is not None and tree.qualified_name == module_id and module_id not in shared


__all__ = (
    "ROOT_PYPROJECT_PATH",
    "materialize_blobs_in_scratch",
    "package_marker_files",
    "python_module_ids",
    "root_pyproject_file",
    "scratch_tree_off_loop",
    "split_hits_by_module_id",
)
