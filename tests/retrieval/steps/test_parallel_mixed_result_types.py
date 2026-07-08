"""ParallelStep — mixed-type branch results (ChunkList vs ModuleMemberList).

Regression test for a gap where ``_merge_branch_results`` picks the
FIRST non-None branch's result type but then appends items from EVERY
branch regardless of type. A hybrid YAML with one chunk-search branch
and one member-search branch is plausible (``branches: [...]`` fans out
independent sub-pipelines), and nothing upstream stops a chunk branch
and a member branch from being wired side by side.

Two failure modes are pinned here:

1. **Type corruption.** A ``ModuleMember`` lands inside the merged
   ``ChunkList.items`` tuple (or vice versa) because ``first_type`` is
   fixed by the first branch but the item-accumulation loop does not
   filter by type.
2. **Silent id collision.** ``_key`` uses ``item.id`` with no type
   discrimination, so ``Chunk(id=5)`` and ``ModuleMember(id=5)`` are
   treated as the SAME key — the second item is dropped without any
   diagnostic, even though they are semantically unrelated records.

This test pins TODAY's behavior (whatever it is) so a future fix that
segregates types or raises a diagnostic has to consciously update it,
rather than silently regressing back to corruption.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import pytest

from pydocs_mcp.models import Chunk, ChunkList, ModuleMember, ModuleMemberList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.steps.parallel import ParallelStep


@dataclass(frozen=True, slots=True)
class _ReturnChunksStep(RetrieverStep):
    """Test helper: a branch that finishes with a ChunkList result."""

    items: tuple[Chunk, ...]
    name: str = field(default="chunk_branch", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        return replace(state, result=ChunkList(items=self.items))


@dataclass(frozen=True, slots=True)
class _ReturnMembersStep(RetrieverStep):
    """Test helper: a branch that finishes with a ModuleMemberList result."""

    items: tuple[ModuleMember, ...]
    name: str = field(default="member_branch", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        return replace(state, result=ModuleMemberList(items=self.items))


@pytest.mark.asyncio
async def test_parallel_step_mixed_chunk_and_member_branches_corrupts_or_drops() -> None:
    """Pin today's behavior when one branch returns ChunkList and another
    returns ModuleMemberList.

    ``first_type`` locks to ``ChunkList`` (the first branch), so the
    merged ``state.result`` is materialized as a ``ChunkList`` — but the
    accumulation loop blindly appends the ``ModuleMember`` items too,
    landing non-``Chunk`` objects inside ``ChunkList.items``. Separately,
    ``ModuleMember(id=5)`` collides on key ``5`` with ``Chunk(id=5)`` from
    the first branch, so it is dropped as a "duplicate" even though the
    two records are unrelated types.
    """
    initial = RetrieverState(query=SearchQuery(terms="x", max_results=10))
    parallel = ParallelStep(
        stages=(
            _ReturnChunksStep(items=(Chunk(text="chunk-a", id=5, relevance=0.9),)),
            _ReturnMembersStep(
                items=(
                    ModuleMember(id=5, relevance=0.8),  # colliding id with Chunk(id=5)
                    ModuleMember(id=6, relevance=0.7),  # distinct id
                )
            ),
        ),
    )

    out = await parallel.run(initial)

    # first_type locks to the first branch's type (ChunkList) — the merged
    # result is materialized as a ChunkList even though a ModuleMember was
    # appended into it below.
    assert isinstance(out.result, ChunkList)

    items = out.result.items
    ids = [item.id for item in items]

    # The colliding id=5 ModuleMember is silently dropped (its key
    # collided with Chunk(id=5) from the first branch) — no error, no
    # trace, just a vanished result.
    assert ids.count(5) == 1

    # The non-colliding ModuleMember(id=6) survives the merge and is
    # appended into what is nominally a ChunkList — type corruption:
    # a ModuleMember instance now lives inside ChunkList.items, and it
    # has no ``.text`` attribute the way a Chunk does.
    member_items = [item for item in items if isinstance(item, ModuleMember)]
    assert len(member_items) == 1
    assert member_items[0].id == 6
    assert not hasattr(member_items[0], "text")
