"""DenseScorerStep — post-fusion dense re-rank via turbovec allowlist search.

Mirrors ``tests/retrieval/steps/test_late_interaction_scorer.py``'s shape: a
fake store implementing the read-only score view, driven directly (no real
TurboQuant index) so the step's re-rank / preserve-absent / scratch-discipline
contract is pinned without I/O.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.steps.dense_scorer import DenseScorerStep
from tests._fakes import MockEmbedder


class FakeVectorScoreable:
    """Returns a fixed id -> score map, intersected with the requested subset.

    Mirrors turbovec's allowlist contract: ids absent from the backing
    index (simulated here as "not in the fixed map") are silently skipped,
    never raised on — exactly what real candidates that never made it into
    the ``.tq`` sidecar (BM25-only / selective-embed) look like.
    """

    def __init__(self, id_to_score: dict[int, float]) -> None:
        self._id_to_score = id_to_score

    async def score(
        self,
        query_vector: Sequence[float],
        *,
        subset_chunk_ids: Sequence[int],
        top_k: int,
    ) -> tuple[tuple[int, float], ...]:
        pairs = [
            (cid, self._id_to_score[cid]) for cid in subset_chunk_ids if cid in self._id_to_score
        ]
        pairs.sort(key=lambda p: p[1], reverse=True)
        return tuple(pairs[:top_k])


def _state(candidates: ChunkList | None, terms: str = "hello") -> RetrieverState:
    return RetrieverState(query=SearchQuery(terms=terms), candidates=candidates)


@pytest.mark.asyncio
async def test_present_candidates_reordered_descending_by_score() -> None:
    candidates = ChunkList(
        items=(
            Chunk(text="a", id=1, relevance=0.1, retriever_name="rrf"),
            Chunk(text="b", id=2, relevance=0.9, retriever_name="rrf"),
            Chunk(text="c", id=3, relevance=0.5, retriever_name="rrf"),
        )
    )
    store = FakeVectorScoreable({1: 3.0, 2: 5.0, 3: 1.0})
    step = DenseScorerStep(store=store, embedder=MockEmbedder(dim=4), top_k=10)

    out = await step.run(_state(candidates))

    ids = [c.id for c in out.candidates.items]
    relevances = [c.relevance for c in out.candidates.items]
    assert ids == [2, 1, 3]
    assert relevances == [5.0, 3.0, 1.0]
    assert all(c.retriever_name == "turboquant_dense" for c in out.candidates.items)


@pytest.mark.asyncio
async def test_absent_candidate_kept_and_appended_after_scored_in_original_order() -> None:
    # id=2 and id=4 are absent from the fake's map — simulating BM25-only /
    # not-yet-embedded candidates. They must survive (not be dropped, unlike
    # LateInteractionScorerStep) and keep their RELATIVE incoming order,
    # appended after the descending-sorted scored candidates.
    candidates = ChunkList(
        items=(
            Chunk(text="a", id=1, relevance=0.4, retriever_name="rrf"),
            Chunk(text="b", id=2, relevance=0.3, retriever_name="rrf"),  # absent
            Chunk(text="c", id=3, relevance=0.2, retriever_name="rrf"),
            Chunk(text="d", id=4, relevance=0.1, retriever_name="rrf"),  # absent
        )
    )
    store = FakeVectorScoreable({1: 1.0, 3: 9.0})
    step = DenseScorerStep(store=store, embedder=MockEmbedder(dim=4), top_k=10)

    out = await step.run(_state(candidates))

    ids = [c.id for c in out.candidates.items]
    # Scored (descending: 3 then 1) first, then absent (2, 4) in original order.
    assert ids == [3, 1, 2, 4]
    by_id = {c.id: c for c in out.candidates.items}
    assert by_id[3].relevance == 9.0
    assert by_id[1].relevance == 1.0
    # Absent candidates keep their fused relevance/retriever_name untouched.
    assert by_id[2].relevance == 0.3
    assert by_id[2].retriever_name == "rrf"
    assert by_id[4].relevance == 0.1
    assert by_id[4].retriever_name == "rrf"


@pytest.mark.asyncio
async def test_none_candidates_pass_through() -> None:
    store = FakeVectorScoreable({})
    step = DenseScorerStep(store=store, embedder=MockEmbedder(dim=4), top_k=10)
    out = await step.run(_state(None))
    assert out.candidates is None


@pytest.mark.asyncio
async def test_empty_chunk_list_pass_through() -> None:
    store = FakeVectorScoreable({})
    step = DenseScorerStep(store=store, embedder=MockEmbedder(dim=4), top_k=10)
    out = await step.run(_state(ChunkList(items=())))
    assert out.candidates is not None
    assert out.candidates.items == ()


@pytest.mark.asyncio
async def test_member_candidates_are_noop() -> None:
    from pydocs_mcp.models import ModuleMember, ModuleMemberList

    members = ModuleMemberList(items=(ModuleMember(metadata={"name": "foo", "kind": "function"}),))
    store = FakeVectorScoreable({})
    step = DenseScorerStep(store=store, embedder=MockEmbedder(dim=4), top_k=10)
    out = await step.run(_state(members))  # type: ignore[arg-type]
    assert out.candidates is members


@pytest.mark.asyncio
async def test_publish_to_writes_fresh_scratch_dict() -> None:
    candidates = ChunkList(items=(Chunk(text="a", id=1, relevance=0.5),))
    store = FakeVectorScoreable({1: 2.0})
    step = DenseScorerStep(
        store=store,
        embedder=MockEmbedder(dim=4),
        top_k=10,
        publish_to="dense.reranked",
    )
    src = _state(candidates)
    out = await step.run(src)

    assert "dense.reranked" in out.scratch
    assert out.scratch["dense.reranked"] == out.candidates
    # Fresh scratch dict — no aliasing of the input (CLAUDE.md scratch
    # mutation discipline: this step may run inside a ParallelStep branch).
    assert out.scratch is not src.scratch


def test_from_dict_strict_gate_no_vector_store() -> None:
    ctx = BuildContext(embedder=MockEmbedder(dim=4))
    with pytest.raises(ValueError, match="vector_store"):
        DenseScorerStep.from_dict({}, ctx)


def test_from_dict_strict_gate_no_embedder() -> None:
    ctx = BuildContext(vector_store=FakeVectorScoreable({}))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="embedder"):
        DenseScorerStep.from_dict({}, ctx)


def test_from_dict_strict_gate_vector_store_not_scoreable() -> None:
    class _NotScoreable:
        async def vector_search(self, query_vector, limit, filter=None):
            return ()

    ctx = BuildContext(vector_store=_NotScoreable(), embedder=MockEmbedder(dim=4))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="VectorScoreable"):
        DenseScorerStep.from_dict({}, ctx)


def test_to_from_dict_round_trip() -> None:
    ctx = BuildContext(vector_store=FakeVectorScoreable({}), embedder=MockEmbedder(dim=4))  # type: ignore[arg-type]
    step = DenseScorerStep.from_dict({"top_k": 42, "publish_to": "x"}, ctx)
    out = step.to_dict()
    assert out == {"type": "dense_scorer", "top_k": 42, "publish_to": "x"}


def test_to_dict_omits_defaults() -> None:
    step = DenseScorerStep(store=FakeVectorScoreable({}), embedder=MockEmbedder(dim=4))  # type: ignore[arg-type]
    out = step.to_dict()
    assert out == {"type": "dense_scorer"}
