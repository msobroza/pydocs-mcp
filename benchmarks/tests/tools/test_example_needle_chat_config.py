"""The chat slice's serving config carries the pinned before/after llm block, field for field.

Two copies of one block is the price of a config a person can serve by hand; this
pin is what keeps them one decision. The block file stays the single source the
before/after run and the repro runner's ``--llm-block`` read.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_CONFIGS = Path(__file__).parents[2] / "configs"
_CHAT_CONFIG = _CONFIGS / "ask_openrouter_example_needle_chat.yaml"
_PINNED_BLOCK = _CONFIGS / "ask_openrouter_qwen3_8_27b_llm.yaml"


def _served_llm_block() -> dict:
    return yaml.safe_load(_CHAT_CONFIG.read_text(encoding="utf-8"))["ask_your_docs"]["llm"]


def test_the_served_llm_block_equals_the_pinned_block_field_for_field() -> None:
    pinned = yaml.safe_load(_PINNED_BLOCK.read_text(encoding="utf-8"))
    served = _served_llm_block()

    assert {key: served.get(key) for key in pinned} == pinned


def test_the_served_config_adds_only_the_model_and_the_vision_rule() -> None:
    pinned = yaml.safe_load(_PINNED_BLOCK.read_text(encoding="utf-8"))

    extra = {key: value for key, value in _served_llm_block().items() if key not in pinned}

    assert extra == {"model": "qwen/qwen3.8-27b", "vision": True}


def test_the_served_config_pins_the_embedder_the_records_pin() -> None:
    embedding = yaml.safe_load(_CHAT_CONFIG.read_text(encoding="utf-8"))["embedding"]

    assert (embedding["model_name"], embedding["dim"]) == ("qwen/qwen3-embedding-4b", 2560)


def test_the_served_config_is_a_valid_product_config() -> None:
    pytest.importorskip("pydocs_mcp")
    from pydocs_mcp.retrieval.config.app_config import AppConfig

    config = AppConfig.load(explicit_path=_CHAT_CONFIG)

    assert config.ask_your_docs.llm is not None
    assert config.ask_your_docs.llm.model == "qwen/qwen3.8-27b"
    assert config.embedding.model_name == "qwen/qwen3-embedding-4b"
