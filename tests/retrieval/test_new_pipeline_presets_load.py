"""All four new YAML presets parse + assemble without error (AC-21 prereq).

Validates schema-level contract of the four new chunk-search presets:

- ``chunk_search_dense.yaml`` — dense-only composite (with token_budget_formatter)
- ``chunk_search_dense_ranked.yaml`` — dense-only ranked (no formatter, for benchmarks)
- ``chunk_search_hybrid.yaml`` — BM25 + Dense fused via RRF, composite
- ``chunk_search_hybrid_ranked.yaml`` — BM25 + Dense fused via RRF, ranked

Pure YAML parsing — no pipeline instantiation. Building any of these
preset would require :class:`BuildContext` with a non-None ``embedder``
and ``vector_store`` (see DenseFetcherStep.from_dict + DenseScorerStep.from_dict);
tests that exercise the build path live alongside the dense steps' own
test modules. These tests only assert "the YAML the project ships parses
and has the right shape".
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PIPELINES_DIR = Path(__file__).resolve().parents[2] / "python" / "pydocs_mcp" / "pipelines"

NEW_PRESETS = (
    "chunk_search_dense.yaml",
    "chunk_search_dense_ranked.yaml",
    "chunk_search_hybrid.yaml",
    "chunk_search_hybrid_ranked.yaml",
)


@pytest.mark.parametrize("preset", NEW_PRESETS)
def test_preset_file_exists_and_parses(preset: str) -> None:
    path = PIPELINES_DIR / preset
    assert path.exists(), f"missing preset {preset}"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "name" in cfg
    assert "steps" in cfg
    assert len(cfg["steps"]) >= 2


def test_hybrid_preset_has_parallel_then_rrf_fusion() -> None:
    cfg = yaml.safe_load(
        (PIPELINES_DIR / "chunk_search_hybrid.yaml").read_text(encoding="utf-8"),
    )
    step_types = [s["type"] for s in cfg["steps"]]
    assert "parallel_retrieval" in step_types
    assert "rrf_fusion" in step_types
    assert step_types.index("parallel_retrieval") < step_types.index("rrf_fusion")


def test_dense_preset_uses_dense_fetcher_and_dense_scorer() -> None:
    cfg = yaml.safe_load(
        (PIPELINES_DIR / "chunk_search_dense.yaml").read_text(encoding="utf-8"),
    )
    step_types = [s["type"] for s in cfg["steps"]]
    assert "dense_fetcher" in step_types
    assert "dense_scorer" in step_types


def test_ranked_variants_drop_token_budget_formatter() -> None:
    for ranked in ("chunk_search_dense_ranked.yaml", "chunk_search_hybrid_ranked.yaml"):
        cfg = yaml.safe_load((PIPELINES_DIR / ranked).read_text(encoding="utf-8"))
        step_types = [s["type"] for s in cfg["steps"]]
        assert "token_budget_formatter" not in step_types
