"""AC-8: shipped defaults include the new ``serve.watch.*`` keys."""
from __future__ import annotations

import importlib.resources
from pathlib import Path

import yaml


def _shipped_yaml() -> dict:
    p = Path(str(importlib.resources.files("pydocs_mcp.defaults").joinpath(
        "default_config.yaml"
    )))
    return yaml.safe_load(p.read_text())


def test_serve_watch_keys_present_in_shipped_defaults() -> None:
    data = _shipped_yaml()
    assert "serve" in data
    assert "watch" in data["serve"]
    watch = data["serve"]["watch"]
    assert watch["enabled"] is False
    assert watch["debounce_ms"] == 500
    # YAML lists become Python lists — pydantic coerces to tuple on load.
    assert ".py" in watch["extensions"]
    assert ".md" in watch["extensions"]
    assert ".ipynb" in watch["extensions"]
    assert any("__pycache__" in g for g in watch["ignore_globs"])
    assert any(".git" in g for g in watch["ignore_globs"])


def test_app_config_load_picks_up_yaml_overrides(tmp_path: Path) -> None:
    """User YAML overlay propagates into AppConfig.serve.watch."""
    from pydocs_mcp.retrieval.config import AppConfig

    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        "serve:\n"
        "  watch:\n"
        "    enabled: true\n"
        "    debounce_ms: 1234\n"
    )
    cfg = AppConfig.load(explicit_path=overlay)
    assert cfg.serve.watch.enabled is True
    assert cfg.serve.watch.debounce_ms == 1234
    # Unspecified keys fall through to shipped defaults.
    assert ".py" in cfg.serve.watch.extensions
