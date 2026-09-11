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
    p = Path(str(importlib.resources.files("pydocs_mcp.defaults").joinpath("default_config.yaml")))
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def test_serve_watch_keys_present_in_shipped_defaults() -> None:
    data = _shipped_yaml()
    assert "serve" in data
    assert "watch" in data["serve"]
    watch = data["serve"]["watch"]
    assert watch["enabled"] is False
    assert watch["debounce_ms"] == 500
    # null = follow extraction.discovery.project.include_extensions (the
    # watcher watches the PROJECT tree, so it follows the project scope).
    assert "extensions" in watch
    assert watch["extensions"] is None
    assert any("__pycache__" in g for g in watch["ignore_globs"])
    assert any(".git" in g for g in watch["ignore_globs"])


def test_app_config_load_picks_up_yaml_overrides(tmp_path: Path) -> None:
    """User YAML overlay propagates into AppConfig.serve.watch.

    Pins that the overlay merges with shipped defaults: the unspecified
    ``extensions`` key falls through to the shipped ``null`` (follow the
    project scope), and ``ignore_globs`` keeps its list-to-tuple coercion.
    """
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("serve:\n  watch:\n    enabled: true\n    debounce_ms: 1234\n")
    cfg = AppConfig.load(explicit_path=overlay)
    assert cfg.serve.watch.enabled is True
    assert cfg.serve.watch.debounce_ms == 1234
    # Unspecified keys fall through to shipped defaults.
    assert cfg.serve.watch.extensions is None
    assert isinstance(cfg.serve.watch.ignore_globs, tuple)


def test_an_explicit_extensions_overlay_is_coerced_to_a_tuple(tmp_path: Path) -> None:
    """An explicit ``serve.watch.extensions`` list survives the load path as
    an immutable tuple — the type ``serve/watcher.py`` consumers rely on."""
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text('serve:\n  watch:\n    extensions: [".py", ".rs"]\n')
    cfg = AppConfig.load(explicit_path=overlay)
    assert cfg.serve.watch.extensions == (".py", ".rs")
    assert isinstance(cfg.serve.watch.extensions, tuple)
