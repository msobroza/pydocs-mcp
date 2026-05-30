"""search_backend YAML overlay parses; default kind is sqlite_composite."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.retrieval.config import AppConfig


def test_default_search_backend_kind_is_sqlite_composite() -> None:
    cfg = AppConfig.load()
    assert cfg.search_backend.kind == "sqlite_composite"


def test_search_backend_overlay_parses(tmp_path: Path) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("search_backend:\n  kind: sqlite_composite\n")
    cfg = AppConfig.load(explicit_path=overlay)
    assert cfg.search_backend.kind == "sqlite_composite"
    # dim/bit_width remain sourced from embedding — single source of truth.
    assert cfg.embedding.dim == 384
