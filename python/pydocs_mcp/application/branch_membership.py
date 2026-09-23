"""Membership swap, extraction cache, the project-scoped GC (spec §6.1), and the
per-branch purge (spec §6.8a).

Functions over an OPEN ``uow`` — called inside ``IndexingService.reindex_package``'s
transaction so membership, cache and GC commit atomically with the chunk diff
(spec §6.3 step 6). Kept out of ``indexing_service.py`` on purpose (§6.14 item 2).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import PurePath
from typing import TYPE_CHECKING, Protocol, cast

from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    Chunk,
    ChunkFilterField,
    ModuleMemberFilterField,
)
from pydocs_mcp.storage.branch_records import BranchRecord, ChunkMembership, FileExtraction

if TYPE_CHECKING:
    from pydocs_mcp.application.branch_manifest import BranchManifest
    from pydocs_mcp.storage.protocols import UnitOfWork

Assignment = tuple[Chunk, int]


class _FreedVectorRemover(Protocol):
    """The slice of ``uow.vectors`` the GC calls. ``UnitOfWork.vectors`` is typed
    ``object`` (spec S15, storage/protocols.py), so the GC names what it uses."""

    async def remove_vectors(self, ids: Sequence[int]) -> None: ...


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


def _in_file_order(spans: list[list[int | None]]) -> list[list[int | None]]:
    """Sort one file's ``[chunk_id, start, end]`` spans by start line.

    ``assignments`` arrives kept-then-added, which is diff order, not file
    order — and ``FileExtraction.chunk_spans`` is documented (and consumed) as
    file order. A span with no start line has no place in that order, so it
    sorts last by chunk id, keeping the JSON deterministic across passes.
    """
    return sorted(spans, key=lambda span: (span[1] is None, span[1] or 0, span[0] or 0))


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
        FileExtraction(
            blob_by_path[p], p, manifest.pipeline_hash, json.dumps(_in_file_order(s)), now
        )
        for p, s in spans.items()
    )


def _is_previous_checkout(other: BranchRecord, manifest: BranchManifest) -> bool:
    """True for another branch stamped from the manifest's own worktree.

    A row with no worktree — a branch indexed from git objects, or a landing
    unit (spec §6.5b) — is never a previous checkout, even when the manifest
    names no worktree either; nor is a landing unit that names one: the column,
    not the path, decides (#307). Retiring those is ``branch_retirement``'s job.
    """
    if other.name == manifest.name or other.is_landing_unit:
        return False
    return other.worktree_path is not None and other.worktree_path == manifest.worktree_path


async def branches_retired_by(uow: UnitOfWork, manifest: BranchManifest) -> tuple[str, ...]:
    """The previous working-tree branches of the manifest's root: every other
    stamped branch of the same worktree, which :func:`write_branch_membership`
    retires."""
    return tuple(
        other.name
        for other in await uow.branches.list_branches()
        if _is_previous_checkout(other, manifest)
    )


async def purge_tree_tier_rows(uow: UnitOfWork, name: str) -> None:
    """Drop one branch's project rows from the five branch-keyed tables (spec §6.1 v18).

    The working-tree stamp runs it for every branch it retires, in the same
    transaction (#307): without it a checkout switch leaves the old branch's
    trees, members, references, scores and decisions behind, unbounded.
    :func:`purge_branch_rows` wraps it with membership, manifest and GC.
    """
    await uow.trees.delete_for_package(PROJECT_PACKAGE_NAME, branch=name)
    await uow.module_members.delete(
        filter={
            ModuleMemberFilterField.PACKAGE.value: PROJECT_PACKAGE_NAME,
            ModuleMemberFilterField.BRANCH.value: name,
        }
    )
    await uow.references.delete_for_package(PROJECT_PACKAGE_NAME, branch=name)
    await uow.node_scores.delete_for_package(PROJECT_PACKAGE_NAME, branch=name)
    await uow.decisions.delete_for_package(PROJECT_PACKAGE_NAME, branch=name)


async def write_branch_membership(
    uow: UnitOfWork, *, manifest: BranchManifest, assignments: Sequence[Assignment], now: float
) -> None:
    """Stamp the branch, swap its manifest and membership, retire the previous
    working-tree branch of the same root (P0 keeps today's one-branch-per-checkout
    semantics; P1 replaces the retire step with the §6.8a retention policy) and
    purge the retired branch's tree-tier rows."""
    for retired in await branches_retired_by(uow, manifest):
        await uow.branch_chunks.delete_for_branch(retired)
        await uow.branches.delete_branch(retired)
        await purge_tree_tier_rows(uow, retired)
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
    """Project chunks no branch references and their vectors, then cache rows
    no manifest references; returns the freed chunk ids.

    The vectors go here, not at each caller (#307): the stamp, the per-branch
    purge and any later pass all free chunks through this one GC, and a caller
    that forgot the vectors would leave them in the ``.tq`` sidecar under
    rowids SQLite reuses.
    """
    removed = await uow.chunks.delete_unreferenced_project_chunks()
    if removed:
        await cast("_FreedVectorRemover", uow.vectors).remove_vectors(list(removed))
    await uow.file_extractions.delete_unreferenced()
    return removed


async def purge_branch_rows(uow: UnitOfWork, name: str) -> tuple[int, ...]:
    """Hard-delete every row under one branch name, then run the refcount GC
    (spec §6.8a purge); returns the freed chunk ids.

    Both membership slices, the manifest, and the branch's project rows in the
    five tree-tier tables go; the ``branches`` record stays as the tombstone.
    The GC then frees the project chunks no other branch references, with
    their vectors, and the cache rows no manifest references. Over an open
    ``uow``: the whole purge commits or rolls back as one.
    """
    await uow.branch_chunks.delete_for_branch(name)
    await uow.branches.replace_files(name, ())
    await purge_tree_tier_rows(uow, name)
    return await collect_project_garbage(uow)


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
    "branches_retired_by",
    "collect_project_garbage",
    "drop_all_branches",
    "extraction_rows",
    "membership_rows",
    "purge_branch_rows",
    "purge_tree_tier_rows",
    "write_branch_membership",
    "write_file_extraction_cache",
)
