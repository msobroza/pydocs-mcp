"""ParallelStep merges each branch's state.scratch into the final state.

Spec §3.1 Decision 3 + AC-21 prereq: branches publish their named ranked
lists into ``state.scratch[<branch>.ranked]`` (via
:class:`TopKFilterStep.publish_to`) so a downstream :class:`RRFFusionStep`
can read them. Before this fix, ParallelStep dropped those writes and
relied on the (racy) shared-dict behaviour of ``dataclasses.replace``;
now each branch runs on an isolated scratch copy and the parent
explicitly merges the keys back into the final state (last-write-wins).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import pytest

from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.steps.parallel import ParallelStep


@dataclass(frozen=True, slots=True)
class _PublishToScratch(RetrieverStep):
    """Test helper: writes a fixed ChunkList into ``state.scratch[publish_to]``.

    Also writes the same payload to ``state.candidates`` and ``state.result``
    so the downstream merge sees a real branch contribution and the test
    catches the scratch propagation as the only meaningful new behaviour.
    """

    publish_to: str = ""
    items: tuple[Chunk, ...] = ()
    name: str = field(default="publish", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        payload = ChunkList(items=self.items)
        new_state = replace(state, candidates=payload, result=payload)
        new_state.scratch[self.publish_to] = payload
        return new_state


@pytest.mark.asyncio
async def test_parallel_branches_publish_to_scratch_keys() -> None:
    """Each branch's scratch writes appear in the merged final state."""
    bm25_items = (Chunk(text="a", id=1, relevance=0.9),)
    dense_items = (Chunk(text="b", id=2, relevance=0.8),)
    parallel = ParallelStep(
        stages=(
            _PublishToScratch(publish_to="bm25.ranked", items=bm25_items),
            _PublishToScratch(publish_to="dense.ranked", items=dense_items),
        ),
    )
    state = RetrieverState(query=SearchQuery(terms="x", max_results=10))
    out = await parallel.run(state)
    assert "bm25.ranked" in out.scratch
    assert "dense.ranked" in out.scratch
    bm25_payload = out.scratch["bm25.ranked"]
    dense_payload = out.scratch["dense.ranked"]
    assert tuple(bm25_payload.items) == bm25_items
    assert tuple(dense_payload.items) == dense_items


@pytest.mark.asyncio
async def test_parallel_preserves_initial_scratch_keys() -> None:
    """Keys present in the input state.scratch survive a branch with no scratch writes."""
    bm25_items = (Chunk(text="a", id=1),)

    @dataclass(frozen=True, slots=True)
    class _Noop(RetrieverStep):
        name: str = "noop"

        async def run(self, state: RetrieverState) -> RetrieverState:
            return state

    parallel = ParallelStep(
        stages=(
            _PublishToScratch(publish_to="bm25.ranked", items=bm25_items),
            _Noop(),
        ),
    )
    state = RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        scratch={"upstream.key": "preserved"},
    )
    out = await parallel.run(state)
    assert out.scratch["upstream.key"] == "preserved"
    assert "bm25.ranked" in out.scratch


@pytest.mark.asyncio
async def test_parallel_branches_use_isolated_scratch_copies() -> None:
    """Concurrent branches must not see each other's scratch writes through the input.

    Without isolation, branch B might observe branch A's published key while
    its own ``run`` is in flight (shared dict + interleaved awaits = race).
    """

    @dataclass(frozen=True, slots=True)
    class _AssertCleanScratch(RetrieverStep):
        forbidden_key: str = ""
        name: str = field(default="assert_clean", kw_only=True)

        async def run(self, state: RetrieverState) -> RetrieverState:
            assert self.forbidden_key not in state.scratch, (
                f"branch saw {self.forbidden_key!r} written by a sibling — "
                "scratch is not isolated"
            )
            return state

    items = (Chunk(text="a", id=1),)
    parallel = ParallelStep(
        stages=(
            _PublishToScratch(publish_to="bm25.ranked", items=items),
            _AssertCleanScratch(forbidden_key="bm25.ranked"),
        ),
    )
    state = RetrieverState(query=SearchQuery(terms="x", max_results=10))
    # Must not raise.
    await parallel.run(state)
