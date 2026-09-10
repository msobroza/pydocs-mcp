"""Branch-dimension value objects (spec §6.1): rows of ``branches``,
``branch_files``, ``branch_chunks`` and ``file_extractions``.

Immutable, like :class:`~pydocs_mcp.storage.node_reference.NodeReference`;
the SQLite repositories map them 1:1 onto their tables.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.models import BranchIndexSource, BranchSlice, BranchStatus, FileChangeKind


@dataclass(frozen=True, slots=True)
class BranchRecord:
    """One row of ``branches`` — identity, lifecycle, and freshness of a branch."""

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
    references columns stay ``None`` until P1 populates and consumes them.
    """

    blob_sha: str
    path: str
    pipeline_hash: str
    chunk_spans: str
    created_at: float
    tree_json: str | None = None
    members_json: str | None = None
    references_json: str | None = None
