"""Pin the dense-seeded LLM tree-rerank overlay to its blueprint + wiring.

The BM25-seeded rerank pool structurally misses golds with near-zero lexical
overlap (full-corpus BM25 ranks 480/246 for two benchmark needles that dense
F2LLM-330M ranks at 1 — PAGEINDEX_DIVS.md F4). This overlay seeds the same
gpt-5.5 tree rerank from the dense fetcher instead, so the only variable vs
repoqa_bm25_tree_rerank_gpt55.yaml is the stage-1 candidate generator.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.retrieval.config import AppConfig

_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"
_CFG = _CONFIGS_DIR / "repoqa_dense_tree_rerank_gpt55.yaml"


def test_overlay_file_exists() -> None:
    assert _CFG.is_file(), f"missing {_CFG}"


def test_overlay_selects_dense_tree_rerank_blueprint() -> None:
    config = AppConfig.load(explicit_path=_CFG)
    routes = config.pipelines["chunk"].routes
    defaults = [r for r in routes if r.default]
    assert len(defaults) == 1, f"expected one default chunk route, got {routes!r}"
    assert defaults[0].pipeline_path == Path("pipelines/exp_dense_tree_rerank.yaml")


def test_overlay_pins_dense_seed_embedder_and_reranker_llm() -> None:
    """Apples-to-apples with the BM25-seeded gpt-5.5 run: same reranker LLM,
    same embedder block as the honest dense-330M baseline."""
    config = AppConfig.load(explicit_path=_CFG)
    assert config.embedding.provider == "sentence_transformers"
    assert config.embedding.model_name == "codefuse-ai/F2LLM-v2-330M"
    assert config.embedding.dim == 896
    assert config.llm.model_name == "gpt-5.5"
