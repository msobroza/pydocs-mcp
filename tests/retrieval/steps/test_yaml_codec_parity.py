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
from pydocs_mcp.retrieval.steps.graph_expand import GraphExpandStep
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from pydocs_mcp.retrieval.steps.parent_rollup import ParentRollupStep
from pydocs_mcp.retrieval.steps.rrf_fusion import RRFFusionStep
from tests._fakes import FakeLlmClient, make_fake_uow_factory


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


def test_graph_and_tree_steps_declare_yaml_keys() -> None:
    assert GraphExpandStep._YAML_KEYS == (
        "top_s",
        "max_depth",
        "decay",
        "directions",
        "kinds",
        "neighbors_per_seed",
        "kind_weights",
        "name",
    )
    assert LlmTreeReasoningStep._YAML_KEYS == (
        "prompt_template",
        "include_references",
        "reference_neighbors_limit",
        "output_scratch_key",
        "name",
        "max_tree_tokens",
        "doc_excerpt",
        "doc_excerpt_max_chars",
        "rerank_candidates",
    )


def test_graph_expand_default_emits_bare_type() -> None:
    step = GraphExpandStep(uow_factory=make_fake_uow_factory())
    assert step.to_dict() == {"type": "graph_expand"}


def test_graph_expand_emits_non_defaults_tuples_as_lists() -> None:
    step = GraphExpandStep(
        uow_factory=make_fake_uow_factory(),
        top_s=5,
        max_depth=2,
        decay=0.5,
        directions=("callers",),
        kinds=("calls",),
        neighbors_per_seed=10,
        name="ge",
    )
    assert step.to_dict() == {
        "type": "graph_expand",
        "top_s": 5,
        "max_depth": 2,
        "decay": 0.5,
        "directions": ["callers"],
        "kinds": ["calls"],
        "neighbors_per_seed": 10,
        "name": "ge",
    }


def test_llm_tree_reasoning_default_emits_bare_type() -> None:
    step = LlmTreeReasoningStep(llm_client=FakeLlmClient(), uow_factory=make_fake_uow_factory())
    assert step.to_dict() == {"type": "llm_tree_reasoning"}


def test_llm_tree_reasoning_emits_non_defaults_in_legacy_order() -> None:
    step = LlmTreeReasoningStep(
        llm_client=FakeLlmClient(),
        uow_factory=make_fake_uow_factory(),
        prompt_template="alt_prompt",
        include_references=True,
        reference_neighbors_limit=3,
        output_scratch_key="alt.ranked",
        name="tree",
        max_tree_tokens=1000,
        doc_excerpt="full",
        doc_excerpt_max_chars=120,
        rerank_candidates=True,
    )
    out = step.to_dict()
    assert out == {
        "type": "llm_tree_reasoning",
        "prompt_template": "alt_prompt",
        "include_references": True,
        "reference_neighbors_limit": 3,
        "output_scratch_key": "alt.ranked",
        "name": "tree",
        "max_tree_tokens": 1000,
        "doc_excerpt": "full",
        "doc_excerpt_max_chars": 120,
        "rerank_candidates": True,
    }
    # Emission order pins the legacy if-chain order (YAML byte-parity).
    assert list(out) == [
        "type",
        "prompt_template",
        "include_references",
        "reference_neighbors_limit",
        "output_scratch_key",
        "name",
        "max_tree_tokens",
        "doc_excerpt",
        "doc_excerpt_max_chars",
        "rerank_candidates",
    ]


def test_parent_rollup_declares_yaml_keys() -> None:
    assert ParentRollupStep._YAML_KEYS == ("min_coverage", "min_coverage_by_kind", "name")


def test_parent_rollup_default_emits_bare_type() -> None:
    step = ParentRollupStep(uow_factory=make_fake_uow_factory())
    assert step.to_dict() == {"type": "parent_rollup"}


def test_parent_rollup_emits_non_defaults_in_key_order_mapping_as_dict() -> None:
    step = ParentRollupStep(
        uow_factory=make_fake_uow_factory(),
        min_coverage=0.4,
        min_coverage_by_kind={"class": 0.25},
        name="pr",
    )
    out = step.to_dict()
    assert out == {
        "type": "parent_rollup",
        "min_coverage": 0.4,
        "min_coverage_by_kind": {"class": 0.25},
        "name": "pr",
    }
    assert list(out) == ["type", "min_coverage", "min_coverage_by_kind", "name"]
    assert type(out["min_coverage_by_kind"]) is dict
