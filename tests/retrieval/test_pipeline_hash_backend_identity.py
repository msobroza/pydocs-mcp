"""Backend identity folds into the ingestion pipeline hash (spec §10)."""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.retrieval.config import AppConfig


def test_changing_backend_kind_changes_pipeline_hash(tmp_path: Path):
    base = AppConfig.load()
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("search_backend:\n  kind: other_backend\n")
    changed = AppConfig.load(explicit_path=overlay)
    assert base.ingestion_pipeline_hash != changed.ingestion_pipeline_hash
