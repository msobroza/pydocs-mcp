"""Shipped defaults document ``embedding.query_prefix`` without enabling it."""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import yaml


def _shipped_text() -> str:
    p = Path(str(importlib.resources.files("pydocs_mcp.defaults").joinpath("default_config.yaml")))
    return p.read_text(encoding="utf-8")


def test_default_config_documents_query_prefix() -> None:
    text = _shipped_text()
    # Commented example only: the shipped default must stay None (verbatim
    # queries), so the key never appears as a live mapping entry.
    assert "query_prefix" not in yaml.safe_load(text)["embedding"]
    example = '#   query_prefix: "Instruct: Given a web search query, retrieve'
    assert example in text
    for guidance in ("double quotes", "query_prompt_name", "seal_config_tier", "PYDOCS_CACHE_DIR"):
        assert guidance in text, f"default_config.yaml query_prefix block must mention {guidance!r}"
