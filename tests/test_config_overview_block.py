"""The overview: config block — get_overview card caps (spec §D17)."""

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig


def test_overview_defaults_present() -> None:
    config = AppConfig.load()
    assert config.overview.max_modules == 20
    assert config.overview.max_communities == 10


def test_overview_overridable_via_overlay(tmp_path) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("overview:\n  max_modules: 5\n  max_communities: 3\n")
    config = AppConfig.load(explicit_path=overlay)
    assert config.overview.max_modules == 5
    assert config.overview.max_communities == 3


def test_overview_caps_out_of_bounds_reject(tmp_path) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("overview:\n  max_modules: 0\n")
    with pytest.raises(ValidationError):
        AppConfig.load(explicit_path=overlay)
