"""The Members-line arm's serving file is the ladder's serving file plus one key (issue #393).

The before/after verb takes one ``--config`` for both arms, so the arm that turns
``search.output.members_line`` on needs its own copy of the serving file. Two
copies of one file is the price; this pin keeps them one decision: every key of
the ladder's file unchanged, and the knob on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_CONFIGS = Path(__file__).parents[2] / "configs"
_LADDER_SERVING = _CONFIGS / "ask_openrouter_qwen3_4b.yaml"
_MEMBERS_LINE_ARM = _CONFIGS / "ask_openrouter_qwen3_4b_members_line.yaml"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_arm_serves_the_ladders_file_plus_the_members_line_only() -> None:
    ladder = _load_yaml(_LADDER_SERVING)
    assert "search" not in ladder  # the ladder's file leaves the knob at its default
    assert _load_yaml(_MEMBERS_LINE_ARM) == {**ladder, "search": {"output": {"members_line": True}}}


def test_the_arm_file_is_a_valid_product_config_with_the_line_on() -> None:
    pytest.importorskip("pydocs_mcp")
    from pydocs_mcp.retrieval.config.app_config import AppConfig

    ladder = AppConfig.load(explicit_path=_LADDER_SERVING)
    arm = AppConfig.load(explicit_path=_MEMBERS_LINE_ARM)

    assert (ladder.search.output.members_line, arm.search.output.members_line) == (False, True)
    assert arm.embedding == ladder.embedding
    assert arm.symbol_card.child_cap == ladder.symbol_card.child_cap
