"""Capability diagnostic renders the active matrix (spec invariant C)."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.search_backend import build_search_backend, format_capabilities


def test_format_capabilities_default(tmp_path: Path):
    be = build_search_backend(AppConfig.load(), db_path=tmp_path / "x.db")
    line = format_capabilities(be)
    assert "SearchBackend" in line
    assert "lexical" in line and "dense" in line
    assert "multi" in line
