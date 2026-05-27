"""AC-8: shipped defaults include the new ``serve.watch.*`` keys."""
from __future__ import annotations

import importlib.resources
from pathlib import Path

import pytest
import yaml

from pydocs_mcp.retrieval.config import AppConfig


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Isolate each test from ambient ``PYDOCS_*`` env vars and a user file
    (mirrors ``tests/retrieval/test_reference_graph_config.py``)."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("PYDOCS_LOG_LEVEL", raising=False)
    monkeypatch.chdir(tmp_path)
    yield


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
    assert ".py" in watch["extensions"]
    assert ".md" in watch["extensions"]
    assert ".ipynb" in watch["extensions"]
    assert any("__pycache__" in g for g in watch["ignore_globs"])
    assert any(".git" in g for g in watch["ignore_globs"])


def test_app_config_load_picks_up_yaml_overrides(tmp_path: Path) -> None:
    """User YAML overlay propagates into AppConfig.serve.watch.

    Pins both (a) the overlay merges with shipped defaults, and (b) the
    pydantic list-to-tuple coercion for ``extensions`` survives the load
    path (so downstream consumers in ``serve/watcher.py`` get the immutable
    type promised by ``WatchConfig`` regardless of whether YAML wrote a list).
    """
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
    # Pydantic coerces YAML lists into tuple-of-str for immutability.
    assert isinstance(cfg.serve.watch.extensions, tuple)
    assert isinstance(cfg.serve.watch.ignore_globs, tuple)
