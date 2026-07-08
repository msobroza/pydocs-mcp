"""Watch-mode ``_on_change`` must NOT inherit ``--force``.

``pydocs-mcp watch . --force`` (or ``serve --watch --force``) forces the
INITIAL index — that's what the user asked for. But ``_on_change`` re-runs
``_run_indexing`` with the same argparse namespace, so ``force=True`` used to
flow into every subsequent file-change reindex: each save wiped the whole
cache (SQLite + ``.tq``) via ``IndexingService.clear_all`` and re-embedded
the project AND all dependencies, defeating the <100ms no-change contract —
and in ``serve --watch`` mode, queries during the re-embed window ran against
an empty index.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pydocs_mcp import __main__ as cli
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import FakeObserver


def _watch_args(tmp_path: Path, *, force: bool) -> argparse.Namespace:
    return argparse.Namespace(
        project=str(tmp_path),
        verbose=False,
        watch=True,
        cache_dir=None,
        no_inspect=True,
        config=None,
        force=force,
    )


async def test_on_change_strips_force_from_reindex(tmp_path: Path, monkeypatch) -> None:
    from pydocs_mcp.serve import watcher as watcher_mod

    monkeypatch.setattr(watcher_mod, "_load_watchdog", lambda: FakeObserver)

    captured: list[argparse.Namespace] = []

    async def _fake_run_indexing(ns: argparse.Namespace) -> None:
        captured.append(ns)

    monkeypatch.setattr(cli, "_run_indexing", _fake_run_indexing)

    args = _watch_args(tmp_path, force=True)
    watch_cfg = AppConfig.load().serve.watch
    _watcher, on_change = cli._build_watcher_and_callback(args, watch_cfg)

    await on_change()

    assert len(captured) == 1
    assert captured[0].force is False, (
        "file-change reindex ran with force=True — every save would wipe "
        "and fully re-embed the cache"
    )
    # The caller's namespace keeps its force flag: the INITIAL pass the user
    # explicitly forced is driven by the original args, not the copy.
    assert args.force is True


async def test_on_change_preserves_other_args(tmp_path: Path, monkeypatch) -> None:
    """The de-forced namespace is a copy, not a rewrite — every other flag
    (project path, inspect mode, config path) must survive untouched."""
    from pydocs_mcp.serve import watcher as watcher_mod

    monkeypatch.setattr(watcher_mod, "_load_watchdog", lambda: FakeObserver)

    captured: list[argparse.Namespace] = []

    async def _fake_run_indexing(ns: argparse.Namespace) -> None:
        captured.append(ns)

    monkeypatch.setattr(cli, "_run_indexing", _fake_run_indexing)

    args = _watch_args(tmp_path, force=False)
    watch_cfg = AppConfig.load().serve.watch
    _watcher, on_change = cli._build_watcher_and_callback(args, watch_cfg)

    await on_change()

    assert len(captured) == 1
    assert captured[0].project == str(tmp_path)
    assert captured[0].no_inspect is True
    assert captured[0].config is None
    assert captured[0].force is False
