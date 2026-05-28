"""Tests for LateInteractionConfig (spec AC-2 + Decision F)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig, LateInteractionConfig


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Isolate each test from ambient ``PYDOCS_*`` env vars and a user file.

    Mirrors the fixture in ``test_config.py`` / ``test_embedding_config.py``
    so ``AppConfig.load()`` resolves only the shipped baseline unless a test
    explicitly sets env or supplies an explicit_path.
    """
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("PYDOCS_LOG_LEVEL", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./pydocs-mcp.yaml
    yield


def test_default_disabled() -> None:
    """Master toggle defaults to False — opt-in only (Decision G)."""
    cfg = LateInteractionConfig()
    assert cfg.enabled is False
    assert cfg.provider == "pylate"
    assert cfg.model_name == "lightonai/LateOn-Code"
    assert cfg.dim == 128
    assert cfg.document_length == 180
    assert cfg.query_length == 32
    assert cfg.pool_factor == 1
    assert cfg.device == "cpu"


def test_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        LateInteractionConfig(unknown_field=1)  # type: ignore[call-arg]


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ValidationError):
        LateInteractionConfig(provider="vespa")  # type: ignore[arg-type]


def test_unknown_device_rejected() -> None:
    with pytest.raises(ValidationError):
        LateInteractionConfig(device="tpu")  # type: ignore[arg-type]


def test_compute_pipeline_hash_stable() -> None:
    cfg = LateInteractionConfig(enabled=True)
    assert cfg.compute_pipeline_hash() == cfg.compute_pipeline_hash()


def test_compute_pipeline_hash_changes_on_model_swap() -> None:
    a = LateInteractionConfig(enabled=True, model_name="lightonai/LateOn-Code")
    b = LateInteractionConfig(enabled=True, model_name="other/model")
    assert a.compute_pipeline_hash() != b.compute_pipeline_hash()


def test_compute_pipeline_hash_changes_on_pool_factor() -> None:
    a = LateInteractionConfig(enabled=True, pool_factor=1)
    b = LateInteractionConfig(enabled=True, pool_factor=2)
    assert a.compute_pipeline_hash() != b.compute_pipeline_hash()


def test_app_config_exposes_late_interaction_default() -> None:
    """``AppConfig.late_interaction`` is always present (Null Object pattern;
    a future ``NullMultiVectorStore`` covers the disabled case)."""
    cfg = AppConfig.load()
    assert isinstance(cfg.late_interaction, LateInteractionConfig)
    assert cfg.late_interaction.enabled is False
