"""TopKFilterStep tests — uniform top-K cutoff for chunks and members."""
from __future__ import annotations

from pydocs_mcp.models import (
    Chunk,
    ChunkList,
    ModuleMember,
    ModuleMemberList,
    SearchQuery,
)
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.top_k_filter import TopKFilterStep


def _chunks_with_relevance(*rels: float) -> ChunkList:
    return ChunkList(items=tuple(
        Chunk(text=f"f{i}", relevance=r, metadata={"title": f"t{i}"})
        for i, r in enumerate(rels)
    ))


def _state(candidates) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=candidates,
    )


async def test_topk_sorts_chunks_by_relevance_desc() -> None:
    step = TopKFilterStep(name="topk", k=3)
    out = await step.run(_state(_chunks_with_relevance(0.5, 1.5, 0.8, 2.0)))
    assert isinstance(out.candidates, ChunkList)
    rels = [c.relevance for c in out.candidates.items]
    assert rels == [2.0, 1.5, 0.8]


async def test_topk_caps_to_k() -> None:
    step = TopKFilterStep(name="topk", k=2)
    out = await step.run(_state(_chunks_with_relevance(1.0, 0.5, 0.2)))
    assert isinstance(out.candidates, ChunkList)
    assert len(out.candidates.items) == 2


async def test_topk_fallback_to_source_order_when_no_relevance() -> None:
    """If no candidate carries a relevance value (no scorer ran upstream),
    keep source order and take the first K.
    """
    chunks = ChunkList(items=tuple(
        Chunk(text=f"f{i}", relevance=None, metadata={"title": f"t{i}"})
        for i in range(5)
    ))
    step = TopKFilterStep(name="topk", k=3)
    out = await step.run(_state(chunks))
    assert isinstance(out.candidates, ChunkList)
    titles = [c.metadata["title"] for c in out.candidates.items]
    assert titles == ["t0", "t1", "t2"]


async def test_topk_works_on_members() -> None:
    members = ModuleMemberList(items=tuple(
        ModuleMember(metadata={"name": f"f{i}", "kind": "function"})
        for i in range(5)
    ))
    step = TopKFilterStep(name="topk", k=2)
    out = await step.run(_state(members))
    assert isinstance(out.candidates, ModuleMemberList)
    assert len(out.candidates.items) == 2


async def test_topk_no_op_on_none_candidates() -> None:
    step = TopKFilterStep(name="topk", k=10)
    out = await step.run(_state(None))
    assert out.candidates is None
