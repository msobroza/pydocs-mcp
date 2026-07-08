"""Pin the mentions-traversal graph overlays to their blueprints + wiring.

The two overlays are the apples-to-apples MENTIONS sweep pair for the shipped
dense+graph default: identical F2LLM-330M embedder and graph params, varying
only MENTIONS capture + traversal (unweighted vs kind_weights {mentions: 0.6}).
Loading through the real AppConfig + step builder catches YAML drift (a typo'd
kind or weight) before a GPU sweep burns on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig

_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"
_OVERLAYS = {
    "repoqa_dense_graph_mentions_f2llm330m.yaml": Path("pipelines/exp_dense_graph_mentions.yaml"),
    "repoqa_dense_graph_mentions_weighted_f2llm330m.yaml": Path(
        "pipelines/exp_dense_graph_mentions_weighted.yaml"
    ),
}


@pytest.mark.parametrize(("overlay", "blueprint"), sorted(_OVERLAYS.items()))
def test_overlay_selects_mentions_blueprint(overlay: str, blueprint: Path) -> None:
    cfg_path = _CONFIGS_DIR / overlay
    assert cfg_path.is_file(), f"missing {cfg_path}"
    config = AppConfig.load(explicit_path=cfg_path)
    defaults = [r for r in config.pipelines["chunk"].routes if r.default]
    assert len(defaults) == 1
    assert defaults[0].pipeline_path == blueprint
    assert (_CONFIGS_DIR / blueprint).is_file(), f"missing {blueprint}"


@pytest.mark.parametrize("overlay", sorted(_OVERLAYS))
def test_overlay_enables_mentions_capture_and_pins_embedder(overlay: str) -> None:
    """Traversing MENTIONS is inert without captured mentions rows, and the
    sweep is only apples-to-apples on the same embedder as the dense baseline."""
    config = AppConfig.load(explicit_path=_CONFIGS_DIR / overlay)
    assert "mentions" in config.reference_graph.capture.kinds
    assert config.embedding.provider == "sentence_transformers"
    assert config.embedding.model_name == "codefuse-ai/F2LLM-v2-330M"
    assert config.embedding.dim == 896
