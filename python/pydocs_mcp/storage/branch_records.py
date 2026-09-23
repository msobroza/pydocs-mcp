"""Branch-dimension value objects (spec §6.1): rows of ``branches``,
``branch_files``, ``branch_chunks``, ``file_extractions`` and (v18)
``landing_patch_ids``.

Immutable, like :class:`~pydocs_mcp.storage.node_reference.NodeReference`;
the SQLite repositories map them 1:1 onto their tables.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.models import (
    BranchIndexSource,
    BranchSlice,
    BranchStatus,
    FileChangeKind,
    LandingKind,
    MergeEvidence,
)


@dataclass(frozen=True, slots=True)
class BranchRecord:
    """One row of ``branches`` — identity, lifecycle, and freshness of a branch,
    or of a landing unit (a row keyed by a landing sha, spec §6.5b)."""

    name: str
    head_sha: str
    source: BranchIndexSource
    pipeline_hash: str
    indexed_at: float
    last_used_at: float
    is_default: bool = False
    base_name: str | None = None
    merge_base_sha: str | None = None
    worktree_path: str | None = None
    status: BranchStatus = BranchStatus.ACTIVE
    merged_into: str | None = None
    retired_at: float | None = None
    purge_after: float | None = None
    pinned: bool = False
    # P1 (spec §6.1 v18). A non-NULL ``landing_kind`` marks a landing unit —
    # a row keyed by a landing sha that carries only a DIFF slice (§6.5b).
    landing_kind: LandingKind | None = None
    landed_at: float | None = None
    # §6.5c: the merge-base pair + slice hash the DIFF slice was generated from.
    diff_generation_key: str | None = None
    # §6.8a: the signal that this branch landed on the base (stamped even when
    # the row is pinned or ``auto_retire_merged`` is off), and the landing
    # commit that carried it — a column of its own because ``merged_into``
    # keeps meaning the base name (ADR 0024 O18).
    merge_evidence: MergeEvidence | None = None
    landing_sha: str | None = None
    # Corroboration only (a prune fetch reported the upstream gone); never evidence.
    upstream_gone: bool = False

    @property
    def is_landing_unit(self) -> bool:
        """True for a landing-unit row; the column, not the name, decides —
        a branch may legitimately be named like a sha."""
        return self.landing_kind is not None


@dataclass(frozen=True, slots=True)
class BranchFile:
    """One row of ``branch_files`` — the manifest entry for one project-relative path."""

    branch: str
    path: str
    blob_sha: str
    change_kind: FileChangeKind = FileChangeKind.UNCHANGED


@dataclass(frozen=True, slots=True)
class ChunkMembership:
    """One row of ``branch_chunks`` — a chunk's membership in a branch plus the
    per-branch span (spans live on membership, not on the shared chunk row)."""

    branch: str
    chunk_id: int
    source_path: str
    start_line: int | None = None
    end_line: int | None = None
    changed: bool = False
    slice: BranchSlice = BranchSlice.TREE


@dataclass(frozen=True, slots=True)
class FileExtraction:
    """One row of ``file_extractions`` — the blob-keyed extraction cache.

    ``chunk_spans`` is JSON ``[[chunk_id, start_line, end_line], ...]`` in file
    order — ascending ``start_line``, with a span that has none sorting last
    (the writer is ``branch_membership._in_file_order``). The tree / members /
    references columns hold the file's artifacts (``application/
    extraction_cache.py``); a row without a tree is P0's spans-only shape and
    never a cache hit. ``pipeline_hash`` names the column, but since P1 it holds
    the extraction key — the pipeline hash plus the per-file extraction
    identity (``file_extraction_cache_key``, #261, #309).
    """

    blob_sha: str
    path: str
    pipeline_hash: str
    chunk_spans: str
    created_at: float
    tree_json: str | None = None
    members_json: str | None = None
    references_json: str | None = None


@dataclass(frozen=True, slots=True)
class LandingPatchId:
    """One row of ``landing_patch_ids`` — the immutable ``--stable`` patch-id of
    a first-parent landing, cached so a base move streams only new landings
    (spec §6.2, §6.8a)."""

    sha: str
    patch_id: str
