"""ADR 0008: ``serve.session_start_context.*`` pydantic sub-model + shipped defaults."""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import pytest
import yaml

from pydocs_mcp.retrieval.config import AppConfig, ServeConfig, SessionStartContextConfig


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Isolate from ambient ``PYDOCS_*`` env vars and a cwd user config file."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    yield


def test_session_start_context_defaults_are_off_and_2000() -> None:
    cfg = SessionStartContextConfig()
    assert cfg.enabled is False
    assert cfg.budget_tokens == 2000


def test_serve_config_carries_session_start_context() -> None:
    cfg = ServeConfig()
    assert isinstance(cfg.session_start_context, SessionStartContextConfig)
    assert cfg.session_start_context.enabled is False


def test_app_config_load_defaults_keep_the_flag_off() -> None:
    cfg = AppConfig.load(explicit_path=None)
    assert cfg.serve.session_start_context.enabled is False
    assert cfg.serve.session_start_context.budget_tokens == 2000


def test_session_start_context_rejects_non_positive_budget() -> None:
    with pytest.raises(ValueError, match="budget_tokens"):
        SessionStartContextConfig(budget_tokens=0)
    with pytest.raises(ValueError, match="budget_tokens"):
        SessionStartContextConfig(budget_tokens=-1)


def test_session_start_context_forbids_extra_keys() -> None:
    with pytest.raises(ValueError):
        SessionStartContextConfig(budget=100)  # type: ignore[call-arg]


def test_shipped_default_yaml_carries_the_keys() -> None:
    p = Path(str(importlib.resources.files("pydocs_mcp.defaults").joinpath("default_config.yaml")))
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    block = data["serve"]["session_start_context"]
    assert block["enabled"] is False
    assert block["budget_tokens"] == 2000


def test_yaml_overlay_flips_the_flag(tmp_path: Path) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        "serve:\n  session_start_context:\n    enabled: true\n    budget_tokens: 512\n"
    )
    cfg = AppConfig.load(explicit_path=overlay)
    assert cfg.serve.session_start_context.enabled is True
    assert cfg.serve.session_start_context.budget_tokens == 512
