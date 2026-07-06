"""Golden to_dict parity for steps migrated to the generic YAML codec.

Pins the CURRENT emission set byte-for-byte — including the deliberate
drift (rrf_fusion / chunk_fetcher never serialize ``name``;
centrality_prior does) — so the codec migration cannot change any YAML
round-trip. Unifying the name drift is a follow-up, not this PR.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.steps.centrality_prior import CentralityPriorStep
from pydocs_mcp.retrieval.steps.chunk_fetcher import ChunkFetcherStep
from pydocs_mcp.retrieval.steps.rrf_fusion import RRFFusionStep
from tests._fakes import make_fake_uow_factory


class _FakeProvider:
    cache_path = Path("unused.db")


class _FakeFilterAdapter:
    def adapt(self, tree, *, target_field):
        return ("", ())


def test_migrated_steps_declare_yaml_keys() -> None:
    assert RRFFusionStep._YAML_KEYS == ("k", "branch_keys")
    assert CentralityPriorStep._YAML_KEYS == ("metric", "alpha", "name")
    assert ChunkFetcherStep._YAML_KEYS == ("limit", "retriever_name")


def test_rrf_fusion_default_emits_bare_type() -> None:
    assert RRFFusionStep().to_dict() == {"type": "rrf_fusion"}


def test_rrf_fusion_emits_non_defaults_and_never_name() -> None:
    step = RRFFusionStep(k=10, branch_keys=("a.ranked", "b.ranked"), name="custom")
    assert step.to_dict() == {
        "type": "rrf_fusion",
        "k": 10,
        "branch_keys": ["a.ranked", "b.ranked"],
    }


def test_rrf_fusion_from_dict_round_trip() -> None:
    step = RRFFusionStep.from_dict(
        {"type": "rrf_fusion", "k": 10, "branch_keys": ["a.ranked"]}, BuildContext()
    )
    assert step.k == 10
    assert step.branch_keys == ("a.ranked",)


def test_centrality_prior_default_emits_bare_type() -> None:
    step = CentralityPriorStep(uow_factory=make_fake_uow_factory())
    assert step.to_dict() == {"type": "centrality_prior"}


def test_centrality_prior_emits_non_defaults_including_name() -> None:
    step = CentralityPriorStep(
        uow_factory=make_fake_uow_factory(), metric="in_degree", alpha=0.9, name="cp"
    )
    assert step.to_dict() == {
        "type": "centrality_prior",
        "metric": "in_degree",
        "alpha": 0.9,
        "name": "cp",
    }


def test_chunk_fetcher_default_emits_bare_type() -> None:
    step = ChunkFetcherStep(provider=_FakeProvider(), filter_adapter=_FakeFilterAdapter())
    assert step.to_dict() == {"type": "chunk_fetcher"}


def test_chunk_fetcher_emits_limit_and_retriever_name_never_name() -> None:
    step = ChunkFetcherStep(
        provider=_FakeProvider(),
        filter_adapter=_FakeFilterAdapter(),
        limit=7,
        retriever_name="alt",
        name="custom",
    )
    assert step.to_dict() == {
        "type": "chunk_fetcher",
        "limit": 7,
        "retriever_name": "alt",
    }
