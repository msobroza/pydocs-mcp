"""The multiset chunk diff by content hash (#69), shared by both write paths.

The working-tree pass (``IndexingService._diff_merge_chunks``) diffs a package's
incoming chunks against its persisted rows; the branch pass (#310, spec §6.3
step 4) diffs a branch's extracted misses against the global project pool minus
the rows its cache hits already claim. Both need the same rule, so it lives
here, pure: no store, no write — the caller applies the removal policy.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.models import Chunk


@dataclass(frozen=True, slots=True)
class ChunkDiffOutcome:
    """What the multiset diff decided (spec §6.14 item 3) — the caller writes."""

    removed_ids: tuple[int, ...]
    added_chunks: tuple[Chunk, ...]
    kept_assignments: tuple[tuple[Chunk, int], ...]


def _ids_by_hash(existing_pairs: Sequence[tuple[int, str | None]]) -> dict[str, list[int]]:
    """Persisted ids grouped by hash; a NULL / empty hash never matches, so its
    rows land on the removed side and self-heal (spec AC-8)."""
    grouped: dict[str, list[int]] = defaultdict(list)
    for chunk_id, content_hash in existing_pairs:
        if content_hash:
            grouped[content_hash].append(chunk_id)
    return grouped


def _chunks_by_hash(incoming: Sequence[Chunk]) -> dict[str, list[Chunk]]:
    grouped: dict[str, list[Chunk]] = defaultdict(list)
    for chunk in incoming:
        grouped[chunk.content_hash].append(chunk)
    return grouped


def diff_chunks_by_content_hash(
    existing_pairs: Sequence[tuple[int, str | None]], incoming: Sequence[Chunk]
) -> ChunkDiffOutcome:
    """Per hash, keep ``min(existing, incoming)`` rows (the first existing ids),
    remove the existing excess, add the incoming excess.

    MULTISET, not set (#69): identical chunks legitimately repeat, and a set
    diff would drop one of two duplicates' rows or never insert a second copy.

    Example: existing ``[(1, "h"), (2, "h")]`` and one incoming chunk of hash
    ``"h"`` keep id 1 for it and report id 2 removed.
    """
    existing = _ids_by_hash(existing_pairs)
    wanted = _chunks_by_hash(incoming)
    removed = [chunk_id for chunk_id, content_hash in existing_pairs if not content_hash]
    added: list[Chunk] = []
    kept: list[tuple[Chunk, int]] = []
    for content_hash, ids in existing.items():
        copies = wanted.get(content_hash, [])
        keep = min(len(ids), len(copies))
        removed.extend(ids[keep:])
        added.extend(copies[keep:])
        kept.extend(zip(copies[:keep], ids[:keep], strict=True))
    for content_hash, copies in wanted.items():
        if content_hash not in existing:
            added.extend(copies)
    return ChunkDiffOutcome(tuple(removed), tuple(added), tuple(kept))


__all__ = ("ChunkDiffOutcome", "diff_chunks_by_content_hash")
