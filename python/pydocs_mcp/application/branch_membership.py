"""Membership swap, extraction cache, and the project-scoped GC (spec §6.1).

Functions over an OPEN ``uow`` — called inside ``IndexingService.reindex_package``'s
transaction so membership, cache and GC commit atomically with the chunk diff
(spec §6.3 step 6). Kept out of ``indexing_service.py`` on purpose (§6.14 item 2).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import PurePath
from typing import TYPE_CHECKING

from pydocs_mcp.models import Chunk, ChunkFilterField
from pydocs_mcp.storage.branch_records import BranchRecord, ChunkMembership, FileExtraction

if TYPE_CHECKING:
    from pydocs_mcp.application.branch_manifest import BranchManifest
    from pydocs_mcp.storage.protocols import UnitOfWork

Assignment = tuple[Chunk, int]


def _span(chunk: Chunk) -> tuple[str, int | None, int | None]:
    """The chunk's ``(source_path, start_line, end_line)``, path POSIX-normalized.

    The chunkers' ``_relpath`` emits the PLATFORM separator (backslashes on
    Windows) while manifest paths are always POSIX, so joining the two by string
    equality would silently miss there — no membership row, no cache row. Both
    callers normalize through this one helper.
    """
    md = chunk.metadata
    raw = str(md.get(ChunkFilterField.SOURCE_PATH.value) or "")
    return (
        PurePath(raw).as_posix() if raw else "",
        md.get(ChunkFilterField.START_LINE.value),
        md.get(ChunkFilterField.END_LINE.value),
    )


def membership_rows(
    manifest: BranchManifest, assignments: Sequence[Assignment]
) -> tuple[ChunkMembership, ...]:
    rows = []
    for chunk, chunk_id in assignments:
        path, start, end = _span(chunk)
        rows.append(ChunkMembership(manifest.name, chunk_id, path, start, end))
    return tuple(rows)


def extraction_rows(
    manifest: BranchManifest, assignments: Sequence[Assignment], now: float
) -> tuple[FileExtraction, ...]:
    """One cache row per file with a blob id; blank blobs (no git) are skipped."""
    blob_by_path = {f.path: f.blob_sha for f in manifest.files if f.blob_sha}
    spans: dict[str, list[list[int | None]]] = defaultdict(list)
    for chunk, chunk_id in assignments:
        path, start, end = _span(chunk)
        if path in blob_by_path:
            spans[path].append([chunk_id, start, end])
    return tuple(
        FileExtraction(blob_by_path[p], p, manifest.pipeline_hash, json.dumps(s), now)
        for p, s in spans.items()
    )


async def write_branch_membership(
    uow: UnitOfWork, *, manifest: BranchManifest, assignments: Sequence[Assignment], now: float
) -> None:
    """Stamp the branch, swap its manifest and membership, retire the previous
    working-tree branch of the same root (P0 keeps today's one-branch-per-checkout
    semantics; P1 replaces the retire step with the §6.8a retention policy)."""
    for other in await uow.branches.list_branches():
        if other.name != manifest.name and other.worktree_path == manifest.worktree_path:
            await uow.branch_chunks.delete_for_branch(other.name)
            await uow.branches.delete_branch(other.name)
    record = BranchRecord(
        name=manifest.name,
        head_sha=manifest.head_sha,
        source=manifest.source,
        pipeline_hash=manifest.pipeline_hash,
        indexed_at=now,
        last_used_at=now,
        is_default=True,
        worktree_path=manifest.worktree_path,
    )
    await uow.branches.upsert_branch(record)
    await uow.branches.replace_files(manifest.name, manifest.files)
    await uow.branch_chunks.replace_membership(
        manifest.name, membership_rows(manifest, assignments)
    )


async def write_file_extraction_cache(
    uow: UnitOfWork, *, manifest: BranchManifest, assignments: Sequence[Assignment], now: float
) -> None:
    await uow.file_extractions.upsert_many(extraction_rows(manifest, assignments, now))


async def collect_project_garbage(uow: UnitOfWork) -> tuple[int, ...]:
    """Project chunks no branch references, then cache rows no manifest references."""
    removed = await uow.chunks.delete_unreferenced_project_chunks()
    await uow.file_extractions.delete_unreferenced()
    return removed


async def drop_all_branches(uow: UnitOfWork) -> None:
    """The ``remove_package('__project__')`` cascade: every branch, its manifest
    and membership, then the cache rows that just lost their last reference.

    ``clear_all`` does NOT come through here — it wipes the branch tables
    wholesale via ``uow.delete_all()``.
    """
    for record in await uow.branches.list_branches():
        await uow.branch_chunks.delete_for_branch(record.name)
        await uow.branches.delete_branch(record.name)
    await uow.file_extractions.delete_unreferenced()


__all__ = (
    "Assignment",
    "collect_project_garbage",
    "drop_all_branches",
    "extraction_rows",
    "membership_rows",
    "write_branch_membership",
    "write_file_extraction_cache",
)
