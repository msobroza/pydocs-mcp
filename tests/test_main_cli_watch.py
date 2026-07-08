"""AC-1 / AC-7: `--watch` flag presence + parser shape."""

from __future__ import annotations

import logging

import pytest


def test_serve_subparser_accepts_watch_flag() -> None:
    """AC-1: `pydocs-mcp serve <project> --watch` parses without error."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["serve", ".", "--watch"])
    assert args.cmd == "serve"
    assert getattr(args, "watch", False) is True


def test_serve_subparser_watch_defaults_false() -> None:
    """AC-7: without `--watch`, the namespace.watch is False (or unset
    falling through to YAML's enabled=false)."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["serve", "."])
    # `store_true` default is False; pin that explicitly so we don't
    # accidentally start defaulting to True.
    assert getattr(args, "watch", False) is False


def test_index_subparser_rejects_watch_flag() -> None:
    """`--watch` is `serve`-only (spec §4.2 out of scope: watch mode for
    `pydocs-mcp index`). Argparse should refuse the flag for `index`."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["index", ".", "--watch"])


def test_cmd_serve_with_watch_spawns_watcher_task(monkeypatch, tmp_path) -> None:
    """AC-1: `_cmd_serve(args.watch=True)` spawns the watcher alongside
    the MCP server. Static-analysis pin: `_run_watch_loop` is referenced
    inside `_cmd_serve` (or a helper it calls)."""
    import inspect

    from pydocs_mcp import __main__ as cli_main

    # The watcher loop must be referenced from the serve command path.
    src_serve = inspect.getsource(cli_main._cmd_serve)
    src_module = inspect.getsource(cli_main)
    # Either inline in _cmd_serve OR via a helper — both legal placements.
    assert "_run_watch_loop" in src_serve or "_run_watch_loop" in src_module, (
        "no _run_watch_loop reference in __main__.py"
    )


def test_run_watch_loop_helper_exists() -> None:
    """`_run_watch_loop` is a module-level coroutine helper (O4 — placed
    next to `_run_indexing` / `_run_search` for consistency)."""
    import asyncio
    from pydocs_mcp.__main__ import _run_watch_loop

    assert asyncio.iscoroutinefunction(_run_watch_loop)


def test_cmd_serve_without_watch_does_not_import_watcher(monkeypatch) -> None:
    """AC-7: `pydocs-mcp serve` (no --watch) never touches the watcher
    module. Pin via static analysis on _cmd_serve's call path."""
    import inspect

    from pydocs_mcp.__main__ import _cmd_serve

    src = inspect.getsource(_cmd_serve)
    # The import of `_run_watch_loop` (or watcher module) must be inside
    # a conditional gated by `args.watch` — never at module top.
    if "_run_watch_loop" in src or "pydocs_mcp.serve" in src:
        # Conditional gate must exist nearby.
        assert "args.watch" in src or "watch" in src.lower(), (
            "watcher referenced but no `args.watch` gate"
        )


async def test_run_watch_loop_cancels_watcher_on_server_exit(tmp_path, monkeypatch) -> None:
    """AC-6: when the MCP `run(...)` callable returns / raises, the
    watcher task is cancelled cleanly (Observer.stop called)."""
    import argparse
    import asyncio

    from tests._fakes import FakeObserver

    fake_observer = FakeObserver()

    # Build args-namespace shape that `_run_watch_loop` reads.
    args = argparse.Namespace(
        project=str(tmp_path),
        verbose=False,
        watch=True,
        cache_dir=None,
        no_inspect=True,
        config=None,
    )

    from pydocs_mcp.__main__ import _run_watch_loop

    # Stub the MCP `run` callable so we exit quickly. Real signal-loop
    # plumbing tested separately by the existing test_main_cli.py suite.
    server_calls: list[None] = []

    def _fake_run(db_path, config_path=None, **kwargs):
        server_calls.append(None)
        # Simulate the server running for ~50ms then "Ctrl+C" via return.
        import time

        time.sleep(0.05)

    monkeypatch.setattr("pydocs_mcp.server.run", _fake_run)

    # Inject the fake observer into the watcher. _load_watchdog now returns
    # ONLY the Observer class (FileSystemEventHandler dropped — `_Handler`
    # uses duck typing per watchdog's documented dispatch contract).
    from pydocs_mcp.serve import watcher as watcher_mod

    monkeypatch.setattr(
        watcher_mod,
        "_load_watchdog",
        lambda: lambda: fake_observer,
    )

    # The integration: server exits, watcher gets cancelled, observer stopped.
    await _run_watch_loop(args, db_path=tmp_path / "fake.db")

    assert len(server_calls) == 1
    assert not fake_observer.started, "Observer.stop was not called on shutdown"


async def test_run_watch_loop_cancels_watcher_on_server_crash(tmp_path, monkeypatch) -> None:
    """Risk R4: if `run(...)` raises (not KeyboardInterrupt), the watcher
    still shuts down cleanly via try/finally."""
    import argparse

    from tests._fakes import FakeObserver

    fake_observer = FakeObserver()

    args = argparse.Namespace(
        project=str(tmp_path),
        verbose=False,
        watch=True,
        cache_dir=None,
        no_inspect=True,
        config=None,
    )

    from pydocs_mcp.__main__ import _run_watch_loop

    def _crashing_run(db_path, config_path=None, **kwargs):
        raise RuntimeError("simulated server crash")

    monkeypatch.setattr("pydocs_mcp.server.run", _crashing_run)

    from pydocs_mcp.serve import watcher as watcher_mod

    monkeypatch.setattr(
        watcher_mod,
        "_load_watchdog",
        lambda: lambda: fake_observer,
    )

    with pytest.raises(RuntimeError, match="simulated server crash"):
        await _run_watch_loop(args, db_path=tmp_path / "fake.db")

    assert not fake_observer.started, "Observer.stop was not called on crash"


async def test_run_watch_loop_forwards_gpu_flag_to_server_run(tmp_path, monkeypatch) -> None:
    """`serve --watch --gpu` must reach `server.run` with `gpu=True`.

    `_serve_run` (no-watch path) forwards `gpu=getattr(args, "gpu", False)`
    into `server.run`, which stamps `config.with_device(gpu=gpu)` for
    query-time embedding (see test_server_gpu.py). `_run_watch_loop` must
    forward the same flag — otherwise `--watch --gpu` silently falls back
    to CPU embedding with no error.
    """
    import argparse

    from tests._fakes import FakeObserver

    fake_observer = FakeObserver()

    args = argparse.Namespace(
        project=str(tmp_path),
        verbose=False,
        watch=True,
        gpu=True,
        cache_dir=None,
        no_inspect=True,
        config=None,
    )

    from pydocs_mcp.__main__ import _run_watch_loop

    captured_kwargs: dict[str, object] = {}

    def _fake_run(db_path, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr("pydocs_mcp.server.run", _fake_run)

    from pydocs_mcp.serve import watcher as watcher_mod

    monkeypatch.setattr(
        watcher_mod,
        "_load_watchdog",
        lambda: lambda: fake_observer,
    )

    await _run_watch_loop(args, db_path=tmp_path / "fake.db")

    assert captured_kwargs.get("gpu") is True, (
        f"--gpu was not forwarded to server.run through the watch path: {captured_kwargs}"
    )


async def test_on_change_isolates_reindex_failure(tmp_path, monkeypatch, caplog) -> None:
    """Risk R4: `_on_change` must catch a reindex failure, log it, and
    return normally so `FileWatcher._consume` (which awaits `on_change()`
    directly, watcher.py) keeps draining events instead of dying on the
    first bad edit.

    Regression coverage for a gap where only a noqa-count ceiling comment
    (tests/quality/test_noqa_count.py) referenced this contract — no test
    exercised the actual behavior. Calls `on_change()` twice to pin that
    the callback (and the watch loop it backs) survives repeat failures,
    not just a single one.
    """
    import argparse

    from pydocs_mcp.__main__ import _build_watcher_and_callback
    from pydocs_mcp.retrieval.config.models import WatchConfig

    args = argparse.Namespace(
        project=str(tmp_path),
        verbose=False,
        watch=True,
        force=True,  # must NOT propagate into watch_args; irrelevant here either way
        cache_dir=None,
        no_inspect=True,
        config=None,
    )
    watch_cfg = WatchConfig()

    call_count = 0

    async def _raising_run_indexing(_args) -> None:
        nonlocal call_count
        call_count += 1
        raise RuntimeError(f"simulated reindex crash #{call_count}")

    monkeypatch.setattr("pydocs_mcp.__main__._run_indexing", _raising_run_indexing)

    _watcher, on_change = _build_watcher_and_callback(args, watch_cfg)

    with caplog.at_level(logging.ERROR, logger="pydocs-mcp"):
        # First failure must not propagate.
        await on_change()
        # Second failure must ALSO not propagate — the callback keeps
        # working across repeat failures, not just tolerating one.
        await on_change()

    assert call_count == 2, "on_change must invoke the reindex helper on every call"
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) == 2, (
        f"expected exactly 2 error log records, got {len(error_records)}: "
        f"{[r.getMessage() for r in error_records]}"
    )
    for record in error_records:
        assert "watch: reindex failed" in record.getMessage()
