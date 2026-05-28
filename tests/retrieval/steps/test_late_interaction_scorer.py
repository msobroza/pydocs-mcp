"""LateInteractionScorerStep — MaxSim re-ranker over a candidate ChunkList."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest

from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.steps.late_interaction_scorer import LateInteractionScorerStep


class _StubEmbedder:
    dim = 4
    model_name = "stub"

    async def embed_query(self, text: str) -> list[np.ndarray]:
        return [np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)]

    async def embed_chunks(
        self,
        texts: Sequence[str],
    ) -> tuple[list[np.ndarray], ...]:
        raise NotImplementedError


class _StubMVStore:
    """Returns a static (chunk_id, score) ranking filtered to subset."""

    def __init__(self, ranking: tuple[tuple[int, float], ...]) -> None:
        self._ranking = ranking

    async def add_vectors(
        self,
        ids: Sequence[int],
        embeddings: Sequence[list[np.ndarray]],
    ) -> None:
        return None

    async def remove_vectors(self, ids: Sequence[int]) -> None:
        return None

    async def clear_all(self) -> None:
        return None

    async def score(
        self,
        query_embedding: list[np.ndarray],
        *,
        subset_chunk_ids: Sequence[int],
        top_k: int,
    ) -> tuple[tuple[int, float], ...]:
        subset = set(subset_chunk_ids)
        return tuple(
            (cid, score) for (cid, score) in self._ranking if cid in subset
        )[:top_k]


class _StubUoW:
    def __init__(self, ranking: tuple[tuple[int, float], ...]) -> None:
        self.multi_vectors = _StubMVStore(ranking)

    async def __aenter__(self) -> _StubUoW:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _state(candidates: ChunkList | None) -> RetrieverState:
    return RetrieverState(query=SearchQuery(terms="hello"), candidates=candidates)


@pytest.mark.asyncio
async def test_reranks_candidates_by_maxsim() -> None:
    candidates = ChunkList(
        items=(
            Chunk(text="a", id=1, relevance=0.1),
            Chunk(text="b", id=2, relevance=0.9),
            Chunk(text="c", id=3, relevance=0.5),
        )
    )
    ranking = ((2, 5.0), (1, 3.0), (3, 1.0))
    step = LateInteractionScorerStep(
        embedder=_StubEmbedder(),
        uow_factory=lambda: _StubUoW(ranking),
        top_k=3,
    )
    out = await step.run(_state(candidates))
    assert out.candidates is not None
    ids = [c.id for c in out.candidates.items]
    relevances = [c.relevance for c in out.candidates.items]
    assert ids == [2, 1, 3]
    assert relevances == [5.0, 3.0, 1.0]
    assert all(c.retriever_name == "late_interaction" for c in out.candidates.items)


@pytest.mark.asyncio
async def test_publishes_to_scratch_immutably() -> None:
    candidates = ChunkList(items=(Chunk(text="a", id=1),))
    step = LateInteractionScorerStep(
        embedder=_StubEmbedder(),
        uow_factory=lambda: _StubUoW(((1, 1.0),)),
        top_k=10,
        publish_to="late.ranked",
    )
    src = _state(candidates)
    out = await step.run(src)
    assert "late.ranked" in out.scratch
    # Fresh scratch (no aliasing of the input dict).
    assert out.scratch is not src.scratch


@pytest.mark.asyncio
async def test_empty_candidates_pass_through() -> None:
    step = LateInteractionScorerStep(
        embedder=_StubEmbedder(),
        uow_factory=lambda: _StubUoW(()),
        top_k=10,
    )
    out = await step.run(_state(None))
    assert out.candidates is None


@pytest.mark.asyncio
async def test_empty_chunk_list_pass_through() -> None:
    step = LateInteractionScorerStep(
        embedder=_StubEmbedder(),
        uow_factory=lambda: _StubUoW(()),
        top_k=10,
    )
    out = await step.run(_state(ChunkList(items=())))
    # The step short-circuits and returns the original state untouched.
    assert out.candidates is not None
    assert out.candidates.items == ()


def test_from_dict_strict_gate_no_embedder() -> None:
    ctx = BuildContext()
    with pytest.raises(ValueError, match="multi_vector_embedder"):
        LateInteractionScorerStep.from_dict({}, ctx)


def test_from_dict_strict_gate_no_uow_factory() -> None:
    ctx = BuildContext(multi_vector_embedder=_StubEmbedder())
    with pytest.raises(ValueError, match="uow_factory"):
        LateInteractionScorerStep.from_dict({}, ctx)


def test_to_from_dict_round_trip() -> None:
    ctx = BuildContext(
        multi_vector_embedder=_StubEmbedder(),
        uow_factory=lambda: _StubUoW(()),
    )
    step = LateInteractionScorerStep.from_dict(
        {"top_k": 7, "publish_to": "x"},
        ctx,
    )
    out = step.to_dict()
    assert out["type"] == "late_interaction_scorer"
    assert out["top_k"] == 7
    assert out["publish_to"] == "x"


def test_to_dict_omits_defaults() -> None:
    step = LateInteractionScorerStep(
        embedder=_StubEmbedder(),
        uow_factory=lambda: _StubUoW(()),
    )
    out = step.to_dict()
    assert out == {"type": "late_interaction_scorer"}
