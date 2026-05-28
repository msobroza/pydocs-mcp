"""BM25ScorerStep tests — normalizes FTS5 rank into positive relevance scores."""

from __future__ import annotations

from pydocs_mcp.models import (
    Chunk,
    ChunkList,
    ModuleMember,
    ModuleMemberList,
    SearchQuery,
)
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.bm25_scorer import BM25ScorerStep


def _state_with_chunk_relevances(*relevances: float) -> RetrieverState:
    chunks = tuple(
        Chunk(text=f"def f{i}()", relevance=r, metadata={"title": f"t{i}"})
        for i, r in enumerate(relevances)
    )
    return RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=ChunkList(items=chunks),
    )


async def test_scorer_flips_sign_of_fts5_rank() -> None:
    """FTS5 ranks are negative (lower-magnitude-negative = better). Scorer
    flips sign so higher = better — the convention every downstream step
    (TopKFilterStep, RRFFusionStep, …) assumes.
    """
    state = _state_with_chunk_relevances(-2.5, -1.0, -0.5)
    out = await BM25ScorerStep(name="score").run(state)
    assert isinstance(out.candidates, ChunkList)
    scores = [c.relevance for c in out.candidates.items]
    assert scores == [2.5, 1.0, 0.5]


async def test_scorer_no_op_on_empty_candidates() -> None:
    state = RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=ChunkList(items=()),
    )
    out = await BM25ScorerStep(name="score").run(state)
    assert isinstance(out.candidates, ChunkList)
    assert len(out.candidates.items) == 0


async def test_scorer_skips_when_candidates_is_none() -> None:
    """No candidates → no work. State pass-through unchanged."""
    state = RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=None,
    )
    out = await BM25ScorerStep(name="score").run(state)
    assert out.candidates is None


async def test_scorer_skips_member_candidates() -> None:
    """LIKE-based member candidates have no BM25 rank to flip → pass through."""
    state = RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=ModuleMemberList(
            items=(ModuleMember(metadata={"name": "f", "kind": "function"}, relevance=None),)
        ),
    )
    out = await BM25ScorerStep(name="score").run(state)
    assert isinstance(out.candidates, ModuleMemberList)
    # Items unchanged — relevance left as None.
    assert len(out.candidates.items) == 1
    assert out.candidates.items[0].relevance is None
