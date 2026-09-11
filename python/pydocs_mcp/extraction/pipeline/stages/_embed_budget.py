"""Which incoming chunks still need a vector, given what is already persisted.

Shared by :class:`EmbedChunksStage` and :class:`EmbedChunksMultiVectorStage` so
the two embed paths cannot drift on the one rule that keeps them consistent
with ``IndexingService._diff_merge_chunks``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydocs_mcp.models import Chunk


def indices_beyond_persisted_budget(
    chunks: Sequence[Chunk],
    persisted_counts: Mapping[str, int] | None,
) -> list[int]:
    """Positions in ``chunks`` of every copy that exceeds its hash's persisted count.

    The diff-merge is a MULTISET (#69): for each content hash it keeps
    ``min(existing, incoming)`` rows in place and inserts the incoming excess as
    genuinely new rows — and those new rows need vectors. So the skip map is not
    a set to test membership against; it is a per-hash budget of copies that
    already have a persisted vector. The first ``count`` copies of a hash spend
    the budget, every copy after that is returned for embedding. A membership
    test would let a duplicated section skip the embedder and then be inserted
    vectorless — permanently, since its hash stays "known" on every later pass.

    Example: two persisted copies of hash ``H`` and three incoming → returns the
    position of the third copy only.
    """
    budget = dict(persisted_counts or {})
    to_embed: list[int] = []
    for position, chunk in enumerate(chunks):
        remaining = budget.get(chunk.content_hash, 0)
        if remaining > 0:
            budget[chunk.content_hash] = remaining - 1
            continue
        to_embed.append(position)
    return to_embed


__all__ = ("indices_beyond_persisted_budget",)
