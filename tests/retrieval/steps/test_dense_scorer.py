"""DenseScorerStep — cosine sim re-scoring on np.ndarray (AC-18)."""

import numpy as np
import pytest

from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.dense_scorer import DenseScorerStep
from tests._fakes import MockEmbedder


def _cos(u: np.ndarray, v: np.ndarray) -> float:
    nu = float(np.linalg.norm(u))
    nv = float(np.linalg.norm(v))
    if nu == 0.0 or nv == 0.0:
        return 0.0
    return float(np.dot(u, v) / (nu * nv))


@pytest.mark.asyncio
async def test_dense_scorer_writes_cosine_similarity_per_candidate() -> None:
    embedder = MockEmbedder(dim=4)
    q_vec = await embedder.embed_query("alpha")
    a_vec = await embedder.embed_query("alpha")
    b_vec = await embedder.embed_query("beta")

    candidates = ChunkList(
        items=(
            Chunk(text="alpha", id=1, embedding=a_vec),
            Chunk(text="beta", id=2, embedding=b_vec),
        )
    )
    state = RetrieverState(
        query=SearchQuery(terms="alpha", max_results=10),
        candidates=candidates,
    )
    step = DenseScorerStep(name="dense_scorer", embedder=embedder)
    out = await step.run(state)
    items = out.candidates.items
    expected_a = _cos(q_vec, a_vec)
    expected_b = _cos(q_vec, b_vec)
    by_id = {c.id: c for c in items}
    assert by_id[1].relevance == pytest.approx(expected_a, rel=1e-5)
    assert by_id[2].relevance == pytest.approx(expected_b, rel=1e-5)


@pytest.mark.asyncio
async def test_dense_scorer_no_candidates_returns_state() -> None:
    embedder = MockEmbedder(dim=4)
    state = RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=None,
    )
    step = DenseScorerStep(name="dense_scorer", embedder=embedder)
    out = await step.run(state)
    assert out.candidates is None
