"""AC-17: Docs reflect shipped tree-reasoning + weighted-fusion behavior."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_extensions_mentions_weighted_fusion_shipped() -> None:
    text = (ROOT / "EXTENSIONS.md").read_text(encoding="utf-8")
    assert "WeightedScoreInterpolationStep" in text
    assert "shipped" in text.lower() or "SHIPPED" in text


def test_extensions_mentions_tree_reasoning_shipped() -> None:
    text = (ROOT / "EXTENSIONS.md").read_text(encoding="utf-8")
    assert "LlmTreeReasoningStep" in text


def test_claude_md_lists_new_steps() -> None:
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "weighted_score_interpolation" in text
    assert "llm_tree_reasoning" in text


def test_default_config_has_llm_section() -> None:
    cfg = (ROOT / "python/pydocs_mcp/defaults/default_config.yaml").read_text(encoding="utf-8")
    assert "llm:" in cfg
    assert "openai" in cfg
