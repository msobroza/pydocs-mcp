"""AC-8: ``serve.watch.*`` pydantic sub-model on ``AppConfig``."""

from __future__ import annotations

import pytest

from pydocs_mcp.retrieval.config import AppConfig, ServeConfig, WatchConfig


def test_watch_config_defaults() -> None:
    cfg = WatchConfig()
    assert cfg.enabled is False
    assert cfg.debounce_ms == 500
    assert cfg.extensions == (".py", ".md", ".ipynb")
    assert "**/__pycache__/**" in cfg.ignore_globs
    assert "**/.git/**" in cfg.ignore_globs
    assert "**/.venv/**" in cfg.ignore_globs
    assert "**/*.pyc" in cfg.ignore_globs


def test_serve_config_defaults() -> None:
    cfg = ServeConfig()
    assert isinstance(cfg.watch, WatchConfig)
    assert cfg.watch.enabled is False


def test_app_config_serve_field_present() -> None:
    """``AppConfig.serve`` is reachable from a freshly-loaded config."""
    cfg = AppConfig.load(explicit_path=None)
    assert isinstance(cfg.serve, ServeConfig)
    assert isinstance(cfg.serve.watch, WatchConfig)


def test_watch_config_rejects_zero_debounce() -> None:
    with pytest.raises(ValueError, match="debounce_ms"):
        WatchConfig(debounce_ms=0)


def test_watch_config_rejects_negative_debounce() -> None:
    with pytest.raises(ValueError, match="debounce_ms"):
        WatchConfig(debounce_ms=-1)


def test_watch_config_rejects_too_large_debounce() -> None:
    """60_000 ms ceiling — anything larger and the user would be better off
    re-running `pydocs-mcp index .` manually."""
    with pytest.raises(ValueError, match="debounce_ms"):
        WatchConfig(debounce_ms=60_000)
    with pytest.raises(ValueError, match="debounce_ms"):
        WatchConfig(debounce_ms=120_000)


def test_watch_config_accepts_valid_debounce() -> None:
    WatchConfig(debounce_ms=1)
    WatchConfig(debounce_ms=500)
    WatchConfig(debounce_ms=59_999)


def test_watch_config_forbids_extra_keys() -> None:
    """Typo-catching: ``extentions`` (sic) must fail load, not silently drop."""
    with pytest.raises(ValueError):
        WatchConfig(extentions=[".py"])  # type: ignore[call-arg]


def test_serve_config_forbids_extra_keys() -> None:
    with pytest.raises(ValueError):
        ServeConfig(unknown=True)  # type: ignore[call-arg]
