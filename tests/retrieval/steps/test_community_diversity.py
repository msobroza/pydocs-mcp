"""CommunityDiversityStep — MMR reorder that spreads results across communities."""

from __future__ import annotations

import pytest

from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.retrieval.steps.community_diversity import (
    _DEFAULT_LAMBDA,
    CommunityDiversityStep,
)
from pydocs_mcp.storage.node_score import NodeScore
from tests._fakes import InMemoryNodeScoreStore, make_fake_uow_factory


def _chunk(qname: str, relevance: float) -> Chunk:
    return Chunk(
        text="x", relevance=relevance, metadata={"qualified_name": qname, "package": "pkg"}
    )


def _factory(scores: list[NodeScore]):
    store = InMemoryNodeScoreStore()
    store.by_key = {(s.package, s.qualified_name): s for s in scores}
    return make_fake_uow_factory(node_scores=store)


def _state(items: list[Chunk]) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="q"),
        candidates=ChunkList(items=tuple(items)),
        result=None,
        scratch={},
    )


def _ctx() -> BuildContext:
    return BuildContext(uow_factory=make_fake_uow_factory())


def test_to_dict_omits_defaults() -> None:
    assert CommunityDiversityStep(uow_factory=make_fake_uow_factory()).to_dict() == {
        "type": "community_diversity"
    }


def test_to_dict_round_trip_via_registry() -> None:
    original = CommunityDiversityStep(uow_factory=make_fake_uow_factory(), penalty=0.8)
    rebuilt = step_registry.build(original.to_dict(), _ctx())
    assert isinstance(rebuilt, CommunityDiversityStep)
    assert rebuilt.penalty == 0.8


def test_from_dict_requires_uow_factory() -> None:
    with pytest.raises(ValueError, match="uow_factory"):
        CommunityDiversityStep.from_dict(
            {"type": "community_diversity"}, BuildContext(uow_factory=None)
        )


def test_default_lambda() -> None:
    assert CommunityDiversityStep(uow_factory=make_fake_uow_factory()).penalty == _DEFAULT_LAMBDA


@pytest.mark.asyncio
async def test_diversifies_across_communities() -> None:
    # Ranked dense order: a0(0.9), a1(0.8) [community 0], b0(0.7) [community 1].
    # With a strong penalty, the rank-2 slot should jump to b0 (a new community)
    # instead of a1 (same community as a0).
    scores = [
        NodeScore("pkg", "pkg.a0", community=0),
        NodeScore("pkg", "pkg.a1", community=0),
        NodeScore("pkg", "pkg.b0", community=1),
    ]
    step = CommunityDiversityStep(uow_factory=_factory(scores), penalty=0.5)
    out = await step.run(
        _state([_chunk("pkg.a0", 0.9), _chunk("pkg.a1", 0.8), _chunk("pkg.b0", 0.7)])
    )
    order = [c.metadata["qualified_name"] for c in out.candidates.items]
    assert order == ["pkg.a0", "pkg.b0", "pkg.a1"]


@pytest.mark.asyncio
async def test_unassigned_community_not_penalised() -> None:
    # community -1 nodes never collide with each other -> pure relevance order.
    scores = [NodeScore("pkg", "pkg.x", community=-1), NodeScore("pkg", "pkg.y", community=-1)]
    step = CommunityDiversityStep(uow_factory=_factory(scores), penalty=0.9)
    out = await step.run(_state([_chunk("pkg.x", 0.9), _chunk("pkg.y", 0.8)]))
    assert [c.metadata["qualified_name"] for c in out.candidates.items] == ["pkg.x", "pkg.y"]


@pytest.mark.asyncio
async def test_empty_candidates_unchanged() -> None:
    step = CommunityDiversityStep(uow_factory=_factory([]))
    state = RetrieverState(query=SearchQuery(terms="q"), candidates=None, result=None, scratch={})
    assert await step.run(state) is state
