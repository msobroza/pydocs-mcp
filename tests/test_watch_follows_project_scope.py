"""The file watcher follows the project discovery scope by default.

``serve.watch.extensions: null`` (the shipped default) means "watch every
extension ``extraction.discovery.project.include_extensions`` indexes"; an
explicit list overrides it. ``resolve_watch_extensions`` (``serve/watcher.py``)
does the resolution and ``_build_watcher_and_callback`` calls it, so
``serve --watch`` and the standalone ``watch`` command share one answer.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from pydocs_mcp.extraction.config import (
    _DEFAULT_PROJECT_INCLUDE_EXTENSIONS,
    DiscoveryScopeConfig,
)
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.config.models import WatchConfig


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate each test from ambient ``PYDOCS_*`` env vars and a cwd config
    file (mirrors ``tests/test_default_config_serve_watch.py``)."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("PYDOCS_LOG_LEVEL", raising=False)
    monkeypatch.chdir(tmp_path)


def _load_with_overlay(tmp_path: Path, overlay_yaml: str) -> AppConfig:
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(overlay_yaml, encoding="utf-8")
    return AppConfig.load(explicit_path=overlay)


def _resolved_watch_extensions(cfg: AppConfig) -> tuple[str, ...]:
    from pydocs_mcp.serve.watcher import resolve_watch_extensions

    return resolve_watch_extensions(cfg.serve.watch, cfg.extraction.discovery.project)


def _watch_args(root: Path) -> argparse.Namespace:
    """Namespace shape the watch composition root reads (mirrors
    ``tests/test_main_cli_watch.py``)."""
    return argparse.Namespace(
        project=str(root),
        verbose=False,
        watch=True,
        force=False,
        cache_dir=None,
        no_inspect=True,
        config=None,
    )


def test_the_default_watch_set_follows_the_project_scope() -> None:
    cfg = AppConfig.load(explicit_path=None)
    assert cfg.serve.watch.extensions is None
    project_scope = tuple(cfg.extraction.discovery.project.include_extensions)
    assert _resolved_watch_extensions(cfg) == project_scope
    assert _resolved_watch_extensions(cfg) == _DEFAULT_PROJECT_INCLUDE_EXTENSIONS


def test_a_narrowed_project_scope_narrows_the_watch_set(tmp_path: Path) -> None:
    cfg = _load_with_overlay(
        tmp_path,
        'extraction:\n  discovery:\n    project:\n      include_extensions: [".py", ".rs"]\n',
    )
    assert _resolved_watch_extensions(cfg) == (".py", ".rs")


def test_an_explicit_watch_list_overrides_the_project_scope(tmp_path: Path) -> None:
    cfg = _load_with_overlay(tmp_path, 'serve:\n  watch:\n    extensions: [".py", ".md"]\n')
    assert _resolved_watch_extensions(cfg) == (".py", ".md")


def test_resolve_watch_extensions_prefers_the_explicit_list() -> None:
    from pydocs_mcp.serve.watcher import resolve_watch_extensions

    scope = DiscoveryScopeConfig(include_extensions=[".py", ".c"])
    assert resolve_watch_extensions(WatchConfig(), scope) == (".py", ".c")
    explicit = WatchConfig(extensions=(".md",))
    assert resolve_watch_extensions(explicit, scope) == (".md",)


async def test_the_composition_root_watcher_reacts_to_rs_and_toml_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the stock defaults, the watcher the ``watch`` command builds fires
    on a ``.rs`` source and a non-manifest ``.toml`` — both project-scope
    extensions the index covers."""
    import pydocs_mcp.__main__ as cli
    from pydocs_mcp.serve import watcher as watcher_mod
    from tests._fakes import FakeObserver

    monkeypatch.setattr(watcher_mod, "_load_watchdog", lambda: FakeObserver)
    built: list[watcher_mod.FileWatcher] = []

    async def _capture_instead_of_running(self, on_change) -> None:
        built.append(self)

    monkeypatch.setattr(watcher_mod.FileWatcher, "run_until_cancelled", _capture_instead_of_running)
    await cli._run_watch_only(_watch_args(tmp_path))

    (watcher,) = built
    assert watcher._matches(watcher.root / "src" / "lib.rs") is True
    assert watcher._matches(watcher.root / "config" / "settings.toml") is True
    assert watcher._matches(watcher.root / "logo.svg") is False
