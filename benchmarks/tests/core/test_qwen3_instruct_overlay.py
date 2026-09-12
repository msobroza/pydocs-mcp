"""The Qwen3-4B query-instruction arms are fixed before any paid call.

Each instruct overlay (``qwen3_4b_instruct.yaml``, ``qwen3_4b_instruct_code.yaml``)
must differ from ``qwen3_4b.yaml`` ONLY by ``embedding.query_prefix`` (so every
arm shares one index and the comparison is purely query-side), and
``qwen3_4b_rerun.yaml`` is the byte-identical A/A arm that measures endpoint
nondeterminism. ``qwen3_4b_instruct`` carries the model card's generic default
instruction; ``qwen3_4b_instruct_code`` carries the authors' published
CodeSearchNetCC code-retrieval instruction, pre-registered before any run.
Neither wording was tuned on a benchmark.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydocs_mcp.retrieval.config import AppConfig

_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"
_MODEL_CARD_PREFIX = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"
)
# Verbatim "CodeSearchNetCCRetrieval" entry of QwenLM/Qwen3-Embedding
# evaluation/task_prompts.json @ 490a766, wrapped in run_mteb.sh's template.
_CODE_RETRIEVAL_PREFIX = (
    "Instruct: Given a code comment, retrieve the code snippet corresponding to that comment."
    "\nQuery:"
)
_INSTRUCT_ARMS = [
    ("qwen3_4b_instruct", _MODEL_CARD_PREFIX),
    ("qwen3_4b_instruct_code", _CODE_RETRIEVAL_PREFIX),
]


def _raw(stem: str) -> dict[str, Any]:
    return yaml.safe_load((_CONFIGS_DIR / f"{stem}.yaml").read_text(encoding="utf-8"))


def _loaded(stem: str) -> AppConfig:
    return AppConfig.load(explicit_path=_CONFIGS_DIR / f"{stem}.yaml")


@pytest.mark.parametrize(("stem", "_prefix"), _INSTRUCT_ARMS)
def test_instruct_shares_index_but_not_query_identity(stem: str, _prefix: str) -> None:
    base, instruct = _loaded("qwen3_4b"), _loaded(stem)
    assert instruct.ingestion_pipeline_hash == base.ingestion_pipeline_hash
    base_identity = base.embedding.compute_query_identity_hash()
    assert instruct.embedding.compute_query_identity_hash() != base_identity


@pytest.mark.parametrize(("stem", "prefix"), _INSTRUCT_ARMS)
def test_instruct_differs_from_base_only_by_query_prefix(stem: str, prefix: str) -> None:
    base, instruct = _raw("qwen3_4b"), _raw(stem)
    instruct_embedding = dict(instruct["embedding"])
    assert instruct_embedding.pop("query_prefix") == prefix
    assert instruct_embedding == base["embedding"]
    assert instruct["pipelines"] == base["pipelines"]


def test_rerun_arm_is_identical_to_base() -> None:
    base, rerun = _raw("qwen3_4b"), _raw("qwen3_4b_rerun")
    assert rerun["embedding"] == base["embedding"]
    assert rerun["pipelines"] == base["pipelines"]
    assert _loaded("qwen3_4b_rerun").embedding.query_prefix is None


@pytest.mark.parametrize(("stem", "expected"), _INSTRUCT_ARMS)
def test_instruct_prefix_loads_with_a_real_newline(stem: str, expected: str) -> None:
    prefix = _loaded(stem).embedding.query_prefix
    assert prefix == expected
    assert "\n" in prefix and not prefix.endswith(" ")


def test_instruct_arms_have_distinct_query_identities() -> None:
    generic, code = _loaded("qwen3_4b_instruct"), _loaded("qwen3_4b_instruct_code")
    code_identity = code.embedding.compute_query_identity_hash()
    assert generic.embedding.compute_query_identity_hash() != code_identity
