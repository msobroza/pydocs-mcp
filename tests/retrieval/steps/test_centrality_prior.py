"""CentralityPriorStep — node-score-driven god-node booster."""

from __future__ import annotations

import pytest

from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.retrieval.steps.centrality_prior import (
    _DEFAULT_ALPHA,
    _DEFAULT_METRIC,
    CentralityPriorStep,
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


def _ctx(uow_factory=None) -> BuildContext:
    return BuildContext(uow_factory=uow_factory or make_fake_uow_factory())


def test_defaults() -> None:
    step = CentralityPriorStep(uow_factory=make_fake_uow_factory())
    assert (step.metric, step.alpha) == (_DEFAULT_METRIC, _DEFAULT_ALPHA)


def test_to_dict_omits_defaults() -> None:
    assert CentralityPriorStep(uow_factory=make_fake_uow_factory()).to_dict() == {
        "type": "centrality_prior"
    }


def test_to_dict_round_trip_via_registry() -> None:
    original = CentralityPriorStep(
        uow_factory=make_fake_uow_factory(), metric="in_degree", alpha=0.3
    )
    rebuilt = step_registry.build(original.to_dict(), _ctx())
    assert isinstance(rebuilt, CentralityPriorStep)
    assert (rebuilt.metric, rebuilt.alpha) == ("in_degree", 0.3)


def test_from_dict_requires_uow_factory() -> None:
    with pytest.raises(ValueError, match="uow_factory"):
        CentralityPriorStep.from_dict({"type": "centrality_prior"}, BuildContext(uow_factory=None))


def test_from_dict_rejects_bad_metric() -> None:
    with pytest.raises(ValueError, match="metric"):
        CentralityPriorStep.from_dict({"type": "centrality_prior", "metric": "bogus"}, _ctx())


@pytest.mark.asyncio
async def test_boosts_central_node_above_tie() -> None:
    # Two equal-dense candidates; the higher-PageRank one should sort first.
    scores = [
        NodeScore("pkg", "pkg.hub", pagerank=1.0),
        NodeScore("pkg", "pkg.leaf", pagerank=0.0),
    ]
    step = CentralityPriorStep(uow_factory=_factory(scores), alpha=0.5)
    out = await step.run(_state([_chunk("pkg.leaf", 0.5), _chunk("pkg.hub", 0.5)]))
    order = [c.metadata["qualified_name"] for c in out.candidates.items]
    assert order[0] == "pkg.hub"
    # hub relevance boosted by alpha*1.0 (normalised peak); leaf unchanged.
    by = {c.metadata["qualified_name"]: c.relevance for c in out.candidates.items}
    assert by["pkg.hub"] == pytest.approx(0.5 * (1 + 0.5 * 1.0))
    assert by["pkg.leaf"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_empty_candidates_unchanged() -> None:
    step = CentralityPriorStep(uow_factory=_factory([]))
    state = RetrieverState(query=SearchQuery(terms="q"), candidates=None, result=None, scratch={})
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_no_scores_unchanged() -> None:
    # Empty node_scores table (feature disabled) -> pass through.
    step = CentralityPriorStep(uow_factory=_factory([]))
    state = _state([_chunk("pkg.a", 0.9)])
    assert await step.run(state) is state
