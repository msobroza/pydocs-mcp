"""TopKFilterStep.publish_to writes its output to state.scratch (AC-20)."""

from __future__ import annotations

import pytest

from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.top_k_filter import TopKFilterStep


def _state_with_candidates() -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=ChunkList(
            items=(
                Chunk(text="a", id=1, relevance=0.9),
                Chunk(text="b", id=2, relevance=0.7),
                Chunk(text="c", id=3, relevance=0.5),
            )
        ),
    )


@pytest.mark.asyncio
async def test_publish_to_default_none_does_not_touch_scratch() -> None:
    state = _state_with_candidates()
    step = TopKFilterStep(name="topk", k=2)
    out = await step.run(state)
    assert "bm25.ranked" not in out.scratch
    assert out.candidates is not None  # still in state.candidates


@pytest.mark.asyncio
async def test_publish_to_writes_topk_to_scratch() -> None:
    state = _state_with_candidates()
    step = TopKFilterStep(name="topk", k=2, publish_to="bm25.ranked")
    out = await step.run(state)
    assert "bm25.ranked" in out.scratch
    payload = out.scratch["bm25.ranked"]
    items = tuple(payload.items) if hasattr(payload, "items") else tuple(payload)
    assert len(items) == 2
    assert items[0].id == 1
    assert items[1].id == 2
