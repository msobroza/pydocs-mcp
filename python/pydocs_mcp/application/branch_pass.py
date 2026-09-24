"""One git-objects branch pass over one unit of work (spec §6.3 steps 2, 4-6; #310).

The working-tree pass keeps going through ``IndexingService.reindex_package``;
this is the flow for a ref that is not checked out. Cache hits contribute
membership and tree-tier rows without parsing; the misses arrive already
extracted from the scratch tree. The chunk diff runs against the whole project
pool — every branch's rows — so a chunk another branch already holds is shared,
not re-embedded. Nothing is deleted here: the refcount GC collects what no
branch references. Everything commits at once, so a crash leaves the branch's
previous membership intact.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass

from pydocs_mcp.application.branch_manifest import BranchManifest
from pydocs_mcp.application.branch_membership import (
    Assignment,
    collect_project_garbage,
    membership_rows,
    purge_tree_tier_rows,
    refuse_served_branch,
    stamp_git_objects_branch,
    write_file_extraction_cache,
)
from pydocs_mcp.application.chunk_multiset_diff import diff_chunks_by_content_hash
from pydocs_mcp.application.extraction_cache import CachedFile, ReferenceSweep, file_artifacts
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.protocols import ExtractionResult
from pydocs_mcp.application.tree_tier_branch import stamp_member_branch
from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, ChunkFilterField, ModuleMember
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.protocols import UnitOfWork

log = logging.getLogger("pydocs-mcp")

_SIMILAR_ONLY = (ReferenceKind.SIMILAR,)


@dataclass(frozen=True, slots=True)
class BranchExtraction:
    """The misses of one pass, extracted from the scratch tree (spec §6.3 step 3)."""

    result: ExtractionResult
    # Static members of the missed files' modules only: the hits bring theirs.
    members: tuple[ModuleMember, ...]
    # The project-relative POSIX paths extracted — the cache rows they refill.
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BranchPassInput:
    manifest: BranchManifest
    cached: tuple[CachedFile, ...]
    extraction: BranchExtraction | None
    now: float


@dataclass(frozen=True, slots=True)
class BranchPassOutcome:
    """The counts of one pass — the ``branch_reindex`` log payload (spec R21)."""

    files_total: int
    files_reused: int
    files_extracted: int
    # Chunk rows the pass inserted (and embedded, when vectors are on).
    chunks_embedded: int
    # Membership rows naming a chunk row that already existed.
    chunks_shared: int
    # Chunk rows (and their vectors) the GC freed.
    vectors_removed: int

    @property
    def moved_chunks(self) -> bool:
        """True when a chunk row was inserted or freed: the full-text index is stale."""
        return bool(self.chunks_embedded or self.vectors_removed)


@dataclass(frozen=True, slots=True)
class _MembershipSwap:
    inserted: int
    rows: int


async def run_branch_pass(
    indexing_service: IndexingService,
    uow_factory: Callable[[], UnitOfWork],
    pass_input: BranchPassInput,
) -> BranchPassOutcome:
    """The §6.3 transaction for one branch; returns its counts.

    Raises ``ValueError`` before any write for the served branch — only the
    working-tree pass rewrites it — or a landing unit.
    """
    manifest = pass_input.manifest
    async with uow_factory() as uow:
        await refuse_served_branch(uow, manifest.name)
        swap = await _swap_membership(uow, indexing_service, pass_input)
        await _write_tree_tier(uow, indexing_service, pass_input)
        freed = await collect_project_garbage(
            uow, extraction_cache_key=manifest.extraction_cache_key
        )
        await uow.commit()
    outcome = _outcome(pass_input, swap, freed=len(freed))
    log.info(json.dumps({"event": "branch_reindex", "branch": manifest.name, **asdict(outcome)}))
    return outcome


def _outcome(
    pass_input: BranchPassInput, swap: _MembershipSwap, *, freed: int
) -> BranchPassOutcome:
    extraction = pass_input.extraction
    return BranchPassOutcome(
        files_total=len(pass_input.manifest.files),
        files_reused=len(pass_input.cached),
        files_extracted=len(extraction.paths) if extraction is not None else 0,
        chunks_embedded=swap.inserted,
        chunks_shared=swap.rows - swap.inserted,
        vectors_removed=freed,
    )


# ── Membership (spec §6.3 steps 2, 4, 6) ──


async def _swap_membership(
    uow: UnitOfWork, service: IndexingService, pass_input: BranchPassInput
) -> _MembershipSwap:
    """Pair the misses' chunks with rows, cache the misses, then stamp the branch
    with the hits' rows plus the misses' rows."""
    extraction = pass_input.extraction
    cached_rows = tuple(m for c in pass_input.cached for m in c.memberships)
    assignments: tuple[Assignment, ...] = ()
    inserted = 0
    if extraction is not None:
        claimed = {m.chunk_id for m in cached_rows}
        assignments, inserted = await _assign_chunk_rows(uow, service, extraction, claimed)
        await _cache_misses(uow, pass_input, extraction, assignments)
    rows = (*cached_rows, *membership_rows(pass_input.manifest, assignments))
    await stamp_git_objects_branch(uow, manifest=pass_input.manifest, rows=rows, now=pass_input.now)
    return _MembershipSwap(inserted=inserted, rows=len(rows))


async def _assign_chunk_rows(
    uow: UnitOfWork, service: IndexingService, extraction: BranchExtraction, claimed: set[int]
) -> tuple[tuple[Assignment, ...], int]:
    """Each extracted chunk gets an existing row of its hash, else a new one.

    The pool is every project row, whichever branch holds it (spec §6.3 step
    4), minus the rows this branch's hits already claim: membership is keyed
    ``(branch, chunk_id)``, so a duplicate text gets a row of its own instead
    of one row twice. Returns the pairs and how many rows were inserted.
    """
    pairs = await uow.chunks.list_id_hash_pairs(
        filter={ChunkFilterField.PACKAGE.value: PROJECT_PACKAGE_NAME}
    )
    pool = tuple((chunk_id, h) for chunk_id, h in pairs if chunk_id not in claimed)
    diff = diff_chunks_by_content_hash(pool, extraction.result.chunks)
    added_ids = await service.persist_added_chunks(
        uow, extraction.result.package, diff.added_chunks
    )
    added = tuple(zip(diff.added_chunks, added_ids, strict=True))
    return (*diff.kept_assignments, *added), len(added_ids)


async def _cache_misses(
    uow: UnitOfWork,
    pass_input: BranchPassInput,
    extraction: BranchExtraction,
    assignments: Sequence[Assignment],
) -> None:
    """Cache rows for the extracted files only; a hit's row is already there."""
    result = extraction.result
    artifacts = file_artifacts(
        result.trees,
        extraction.members,
        result.references,
        aliases=result.reference_aliases,
        class_attribute_types=result.class_attribute_types,
        relative_paths=extraction.paths,
    )
    await write_file_extraction_cache(
        uow,
        manifest=pass_input.manifest,
        assignments=assignments,
        now=pass_input.now,
        artifacts=artifacts,
    )


# ── The tree tier (spec §6.3 step 5) ──


async def _write_tree_tier(
    uow: UnitOfWork, service: IndexingService, pass_input: BranchPassInput
) -> None:
    """Replace the branch's rows in the five tree-tier tables.

    The purge also drops any decision row under the name, and none is written:
    decision mining per branch is P2 (O10), so a non-working-tree branch
    carries no decisions — and no GOVERNS edges, which are projected from them.
    """
    name = pass_input.manifest.name
    trees = _branch_trees(pass_input)
    carried = await _carried_similar_edges(uow, pass_input, trees)  # before the purge drops them
    await purge_tree_tier_rows(uow, name)
    await uow.trees.save_many(trees, package=PROJECT_PACKAGE_NAME, branch=name)
    await uow.module_members.upsert_many(_branch_members(pass_input))
    sweeps = _branch_sweeps(pass_input)
    await service.persist_references_for_branch(
        uow,
        references=(*(ref for sweep in sweeps for ref in sweep.references), *carried),
        reference_aliases=_merged_tables(sweep.aliases for sweep in sweeps),
        class_attribute_types=_merged_tables(sweep.class_attribute_types for sweep in sweeps),
        branch=name,
    )


def _branch_trees(pass_input: BranchPassInput) -> tuple[DocumentNode, ...]:
    cached = (c.artifacts.tree for c in pass_input.cached if c.artifacts.tree is not None)
    extraction = pass_input.extraction
    extracted = extraction.result.trees if extraction is not None else ()
    return (*cached, *extracted)


def _branch_members(pass_input: BranchPassInput) -> tuple[ModuleMember, ...]:
    """The hits' members (``cached_file`` stamped them) and the misses', stamped here."""
    cached = (m for c in pass_input.cached for m in c.artifacts.members)
    extraction = pass_input.extraction
    extracted = extraction.members if extraction is not None else ()
    return (*cached, *stamp_member_branch(extracted, pass_input.manifest.name))


def _branch_sweeps(pass_input: BranchPassInput) -> tuple[ReferenceSweep, ...]:
    """Every file's unresolved sweep: the hits' cached ones and the misses' fresh one."""
    cached = tuple(c.artifacts.sweep for c in pass_input.cached)
    if pass_input.extraction is None:
        return cached
    result = pass_input.extraction.result
    fresh = ReferenceSweep(
        result.references, result.reference_aliases, result.class_attribute_types
    )
    return (*cached, fresh)


def _merged_tables(
    tables: Iterable[Mapping[str, Mapping[str, str]]],
) -> dict[str, dict[str, str]]:
    """One writable table from read-only ones (``EMPTY_SWEEP``'s is shared)."""
    return {key: dict(table) for merged in tables for key, table in merged.items()}


async def _carried_similar_edges(
    uow: UnitOfWork, pass_input: BranchPassInput, trees: Sequence[DocumentNode]
) -> tuple[NodeReference, ...]:
    """The kNN ``SIMILAR`` edges out of the reused files (#309 obligation).

    The pass that embedded a chunk derived its edges, so the cache never holds
    them. A hit's chunks keep their rows and vectors, so its edges still hold
    wherever both ends exist on this branch: they are re-derived from the rows
    the served branch and this branch's previous pass hold. The misses' edges
    come fresh from their own embedding.
    """
    sources = _tree_qnames(c.artifacts.tree for c in pass_input.cached)
    if not sources:
        return ()
    targets = _tree_qnames(trees)
    served = await uow.references.list_resolved(_SIMILAR_ONLY)
    own = await uow.references.list_resolved(_SIMILAR_ONLY, branch=pass_input.manifest.name)
    return tuple(
        NodeReference(PROJECT_PACKAGE_NAME, source, target, target, ReferenceKind.SIMILAR)
        for source, target in sorted({*served, *own})
        if source in sources and target in targets
    )


def _tree_qnames(trees: Iterable[DocumentNode | None]) -> set[str]:
    names: set[str] = set()
    pending = [tree for tree in trees if tree is not None]
    while pending:
        node = pending.pop()
        names.add(node.qualified_name)
        pending.extend(node.children)
    return names


__all__ = ("BranchExtraction", "BranchPassInput", "BranchPassOutcome", "run_branch_pass")
