"""Backend identity folds into the ingestion pipeline hash (spec §10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Isolate each test from ambient ``PYDOCS_*`` env vars and a user file."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("PYDOCS_LOG_LEVEL", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./pydocs-mcp.yaml
    yield


def test_changing_backend_kind_changes_pipeline_hash(tmp_path: Path) -> None:
    base = AppConfig.load()
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("search_backend:\n  kind: other_backend\n")
    changed = AppConfig.load(explicit_path=overlay)
    assert base.ingestion_pipeline_hash != changed.ingestion_pipeline_hash
