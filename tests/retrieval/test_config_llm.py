"""AC-2: LlmConfig sub-model + AppConfig.llm wiring + YAML overlay."""
from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

from pydocs_mcp.retrieval.config import AppConfig, LlmConfig


def test_llm_config_defaults() -> None:
    """Defaults: provider=openai, model_name=gpt-4o-mini, temperature=0.0."""
    cfg = LlmConfig()
    assert cfg.provider == "openai"
    assert cfg.model_name == "gpt-4o-mini"
    assert cfg.temperature == 0.0
    assert cfg.max_tokens is None
    assert cfg.api_key is None


def test_app_config_llm_field_present() -> None:
    cfg = AppConfig()
    assert isinstance(cfg.llm, LlmConfig)


def test_app_config_yaml_overlay_for_llm() -> None:
    yaml_text = textwrap.dedent("""
    llm:
      provider: openai
      model_name: gpt-4o
      temperature: 0.2
      max_tokens: 1024
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_text)
        overlay_path = Path(f.name)
    try:
        cfg = AppConfig.load(explicit_path=overlay_path)
        assert cfg.llm.model_name == "gpt-4o"
        assert cfg.llm.temperature == 0.2
        assert cfg.llm.max_tokens == 1024
    finally:
        overlay_path.unlink()
