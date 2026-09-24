"""Membership swap, extraction cache, the project-scoped GC (spec §6.1), and the
per-branch purge (spec §6.8a).

Functions over an OPEN ``uow`` — called inside ``IndexingService.reindex_package``'s
transaction so membership, cache and GC commit atomically with the chunk diff
(spec §6.3 step 6). Kept out of ``indexing_service.py`` on purpose (§6.14 item 2).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, cast

from pydocs_mcp.application.extraction_cache import (
    FileArtifacts,
    artifacts_json,
    posix_source_path,
    require_extraction_cache_key,
)
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
_Spans = list[list[int | None]]
_NO_ARTIFACTS: Mapping[str, FileArtifacts] = MappingProxyType({})
_NO_ARTIFACT_COLUMNS: tuple[str | None, str | None, str | None] = (None, None, None)


class _FreedVectorRemover(Protocol):
    """The slice of ``uow.vectors`` the GC calls. ``UnitOfWork.vectors`` is typed
    ``object`` (spec S15, storage/protocols.py), so the GC names what it uses."""

    async def remove_vectors(self, ids: Sequence[int]) -> None: ...


def _span(chunk: Chunk) -> tuple[str, int | None, int | None]:
    """The chunk's ``(source_path, start_line, end_line)``, path POSIX-normalized
    (:func:`posix_source_path`), or a Windows pass would write no membership
    row and no cache row. Both row builders read spans through here."""
    md = chunk.metadata
    return (
        posix_source_path(str(md.get(ChunkFilterField.SOURCE_PATH.value) or "")),
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


def _in_file_order(spans: _Spans) -> _Spans:
    """Sort one file's ``[chunk_id, start, end]`` spans by start line.

    ``assignments`` arrives kept-then-added, which is diff order, not file
    order — and ``FileExtraction.chunk_spans`` is documented (and consumed) as
    file order. A span with no start line has no place in that order, so it
    sorts last by chunk id, keeping the JSON deterministic across passes.
    """
    return sorted(spans, key=lambda span: (span[1] is None, span[1] or 0, span[0] or 0))


def _spans_by_path(
    assignments: Sequence[Assignment], cached_paths: Mapping[str, str]
) -> dict[str, _Spans]:
    spans: dict[str, _Spans] = defaultdict(list)
    for chunk, chunk_id in assignments:
        path, start, end = _span(chunk)
        if path in cached_paths:
            spans[path].append([chunk_id, start, end])
    return spans


def _extraction_row(
    key: str, blob_sha: str, path: str, spans: _Spans, artifacts: FileArtifacts | None, now: float
) -> FileExtraction:
    tree, members, references = (
        artifacts_json(artifacts) if artifacts is not None else _NO_ARTIFACT_COLUMNS
    )
    return FileExtraction(
        blob_sha,
        path,
        key,
        json.dumps(_in_file_order(spans)),
        now,
        tree_json=tree,
        members_json=members,
        references_json=references,
    )


def extraction_rows(
    manifest: BranchManifest,
    assignments: Sequence[Assignment],
    now: float,
    *,
    artifacts: Mapping[str, FileArtifacts] = _NO_ARTIFACTS,
) -> tuple[FileExtraction, ...]:
    """One cache row per file with a blob id and something to cache — chunk
    spans, artifacts or both — under the manifest's extraction key (#309).

    Blank blobs (no git) are skipped. A file whose tree flattens to no chunk
    still gets a row: without one it would be a miss on every branch. An empty
    file has no tree, so no row.
    """
    key = manifest.extraction_cache_key
    blob_by_path = {f.path: f.blob_sha for f in manifest.files if f.blob_sha}
    spans = _spans_by_path(assignments, blob_by_path)
    tree_only = [p for p in artifacts if p in blob_by_path and p not in spans]
    return tuple(
        _extraction_row(key, blob_by_path[p], p, spans.get(p, []), artifacts.get(p), now)
        for p in (*spans, *tree_only)
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


async def _is_pinned(uow: UnitOfWork, name: str) -> bool:
    # #316: the pin is the operator's (`branches --pin`), not the pass's. The
    # stamp rewrites every other column, and watch mode re-stamps on each save.
    existing = await uow.branches.get_branch(name)
    return existing is not None and existing.pinned


def _stamped_record(
    manifest: BranchManifest, now: float, *, pinned: bool, is_default: bool
) -> BranchRecord:
    """A freshly indexed, ``ACTIVE`` row: a retired row a pass re-indexes is
    re-activated (spec §6.8a), keeping only the operator's pin."""
    return BranchRecord(
        name=manifest.name,
        head_sha=manifest.head_sha,
        source=manifest.source,
        pipeline_hash=manifest.pipeline_hash,
        indexed_at=now,
        last_used_at=now,
        is_default=is_default,
        base_name=manifest.base_name,
        merge_base_sha=manifest.merge_base_sha,
        worktree_path=manifest.worktree_path,
        pinned=pinned,
    )


async def _replace_branch_rows(
    uow: UnitOfWork,
    manifest: BranchManifest,
    rows: Sequence[ChunkMembership],
    now: float,
    *,
    is_default: bool,
) -> None:
    """Upsert the branch row (keeping the operator's pin), then swap its
    manifest and membership — the stamp both write paths share."""
    pinned = await _is_pinned(uow, manifest.name)
    record = _stamped_record(manifest, now, pinned=pinned, is_default=is_default)
    await uow.branches.upsert_branch(record)
    await uow.branches.replace_files(manifest.name, manifest.files)
    await uow.branch_chunks.replace_membership(manifest.name, rows)


async def write_branch_membership(
    uow: UnitOfWork, *, manifest: BranchManifest, assignments: Sequence[Assignment], now: float
) -> None:
    """Stamp the branch (keeping an operator's pin), swap its manifest and
    membership, retire the previous working-tree branch of the same root (P0
    keeps today's one-branch-per-checkout semantics; P1 replaces the retire step
    with the §6.8a retention policy) and purge the retired branch's tree-tier
    rows."""
    for retired in await branches_retired_by(uow, manifest):
        await uow.branch_chunks.delete_for_branch(retired)
        await uow.branches.delete_branch(retired)
        await purge_tree_tier_rows(uow, retired)
    rows = membership_rows(manifest, assignments)
    await _replace_branch_rows(uow, manifest, rows, now, is_default=True)


async def refuse_served_branch(uow: UnitOfWork, name: str) -> None:
    """``ValueError`` when ``name`` is the served row or a landing unit (#310).

    Only the working-tree pass may rewrite the served branch: its cache check
    compares the stamped head, and a git-objects stamp would drop the default
    flag and the worktree path it keys the checkout on. A landing unit's row
    is keyed by a sha and carries only a DIFF slice (spec §6.5b).
    """
    if name == await uow.branches.default_branch_name():
        raise ValueError(
            f"refusing a git-objects pass over the served branch {name!r}: "
            "only the working-tree pass rewrites it"
        )
    existing = await uow.branches.get_branch(name)
    if existing is not None and existing.is_landing_unit:
        raise ValueError(f"refusing a git-objects pass over {name!r}: it is a landing unit")


async def stamp_git_objects_branch(
    uow: UnitOfWork, *, manifest: BranchManifest, rows: Sequence[ChunkMembership], now: float
) -> None:
    """Stamp a branch indexed from git objects: its row (never the default, no
    worktree, the operator's pin kept), manifest and membership (spec §6.3
    step 6, #310). Retires nothing: other branches keep their rows."""
    await _replace_branch_rows(uow, manifest, rows, now, is_default=False)


async def write_file_extraction_cache(
    uow: UnitOfWork,
    *,
    manifest: BranchManifest,
    assignments: Sequence[Assignment],
    now: float,
    artifacts: Mapping[str, FileArtifacts] = _NO_ARTIFACTS,
) -> None:
    rows = extraction_rows(manifest, assignments, now, artifacts=artifacts)
    await uow.file_extractions.upsert_many(rows)


async def collect_project_garbage(
    uow: UnitOfWork, *, extraction_cache_key: str | None
) -> tuple[int, ...]:
    """Project chunks no branch references and their vectors, then the cache
    rows :func:`_collect_extraction_garbage` drops; returns the freed chunk ids.

    The vectors go here, not at each caller (#307): the stamp, the per-branch
    purge and any later pass all free chunks through this one GC, and a caller
    that forgot the vectors would leave them in the ``.tq`` sidecar under
    rowids SQLite reuses. ``extraction_cache_key`` is the pass's key, required
    so a pass cannot forget it (#309 review); a purge knows none, passes None
    and leaves the superseded rows to the next pass.
    """
    if extraction_cache_key is not None:
        require_extraction_cache_key(extraction_cache_key)
    removed = await uow.chunks.delete_unreferenced_project_chunks()
    if removed:
        await cast("_FreedVectorRemover", uow.vectors).remove_vectors(list(removed))
    await _collect_extraction_garbage(uow, extraction_cache_key, removed)
    return removed


async def _collect_extraction_garbage(
    uow: UnitOfWork, extraction_cache_key: str | None, freed_chunk_ids: Sequence[int]
) -> None:
    """Rows naming a freed chunk, rows under any key but the pass's, then rows
    no manifest references.

    WHY the superseded sweep (#261, #309): a settings, grammar or chunker change
    moves the key, so the old rows can never hit again — yet their
    ``(blob_sha, path)`` stays referenced while the file is unchanged, and the
    unreferenced sweep alone would keep them forever, their chunk ids naming
    rows the chunk GC may have freed for SQLite to reuse.

    WHY the freed-chunk sweep (#310): one ``(blob, path, key)`` row serves every
    branch listing that blob, and the last pass to extract the file rewrites
    it — a branch whose package layout moved the file's module id writes ids
    only it holds. Purging that branch frees them while another manifest keeps
    the row referenced; a later hit would then name deleted or reused rows.
    """
    await uow.file_extractions.delete_naming_chunk_ids(freed_chunk_ids)
    if extraction_cache_key is not None:
        await uow.file_extractions.delete_superseded(extraction_cache_key)
    await uow.file_extractions.delete_unreferenced()


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
    # No pass, so no current extraction key: superseded rows wait for the next pass.
    return await collect_project_garbage(uow, extraction_cache_key=None)


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
    "refuse_served_branch",
    "stamp_git_objects_branch",
    "write_branch_membership",
    "write_file_extraction_cache",
)
