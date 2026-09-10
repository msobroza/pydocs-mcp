"""The Qwen3-4B query-instruction arms are fixed before any paid call.

``qwen3_4b_instruct.yaml`` must differ from ``qwen3_4b.yaml`` ONLY by
``embedding.query_prefix`` (so both share one index and the comparison is
purely query-side), and ``qwen3_4b_rerun.yaml`` is the byte-identical A/A arm
that measures endpoint nondeterminism. The prefix is the Qwen3-Embedding
model card's generic default instruction, not a wording tuned on a benchmark.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydocs_mcp.retrieval.config import AppConfig

_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"
_MODEL_CARD_PREFIX = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"
)


def _raw(stem: str) -> dict[str, Any]:
    return yaml.safe_load((_CONFIGS_DIR / f"{stem}.yaml").read_text(encoding="utf-8"))


def _loaded(stem: str) -> AppConfig:
    return AppConfig.load(explicit_path=_CONFIGS_DIR / f"{stem}.yaml")


def test_instruct_shares_index_but_not_query_identity() -> None:
    base, instruct = _loaded("qwen3_4b"), _loaded("qwen3_4b_instruct")
    assert instruct.ingestion_pipeline_hash == base.ingestion_pipeline_hash
    base_identity = base.embedding.compute_query_identity_hash()
    assert instruct.embedding.compute_query_identity_hash() != base_identity


def test_instruct_differs_from_base_only_by_query_prefix() -> None:
    base, instruct = _raw("qwen3_4b"), _raw("qwen3_4b_instruct")
    instruct_embedding = dict(instruct["embedding"])
    assert instruct_embedding.pop("query_prefix") == _MODEL_CARD_PREFIX
    assert instruct_embedding == base["embedding"]
    assert instruct["pipelines"] == base["pipelines"]


def test_rerun_arm_is_identical_to_base() -> None:
    base, rerun = _raw("qwen3_4b"), _raw("qwen3_4b_rerun")
    assert rerun["embedding"] == base["embedding"]
    assert rerun["pipelines"] == base["pipelines"]
    assert _loaded("qwen3_4b_rerun").embedding.query_prefix is None


def test_instruct_prefix_is_the_model_card_string_with_a_real_newline() -> None:
    prefix = _loaded("qwen3_4b_instruct").embedding.query_prefix
    assert prefix == _MODEL_CARD_PREFIX
    assert "\n" in prefix and not prefix.endswith(" ")
