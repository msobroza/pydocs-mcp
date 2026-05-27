"""Tests for the standalone ``pydocs-mcp watch`` subcommand (no MCP server).

Companion to ``test_main_cli_watch.py`` (which covers the
``serve --watch`` path that runs MCP server + watcher concurrently).
This module pins the CLI-only counterpart: the watcher runs in
isolation, NO ``pydocs_mcp.server.run`` call ever happens. Used by
operators who want a fresh index for CLI ``search`` / ``lookup``
without keeping an idle FastMCP stdio process running.
"""
from __future__ import annotations

import argparse
import asyncio

import pytest


def test_watch_subparser_accepts_project_arg() -> None:
    """``pydocs-mcp watch <project>`` parses without error."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["watch", "."])
    assert args.cmd == "watch"
    assert args.project == "."


def test_watch_subparser_rejects_watch_flag() -> None:
    """``pydocs-mcp watch . --watch`` is redundant — argparse should reject.

    ``--watch`` lives on the ``serve`` subparser to enable the dual
    MCP+watcher mode. The standalone ``watch`` subcommand is watch-mode
    by definition, so ``--watch`` would be confusing noise.
    """
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["watch", ".", "--watch"])


def test_cmd_watch_does_not_reference_mcp_run() -> None:
    """Standalone ``watch`` MUST NOT import or call ``pydocs_mcp.server.run``.

    Static-analysis pin so a future refactor that accidentally wires the
    MCP server into the watch-only path breaks loudly here. The whole
    point of ``pydocs-mcp watch`` is to skip the FastMCP stdio loop.
    """
    import inspect

    from pydocs_mcp.__main__ import _cmd_watch, _run_watch_only

    src_cmd = inspect.getsource(_cmd_watch)
    src_loop = inspect.getsource(_run_watch_only)
    combined = src_cmd + src_loop
    assert "pydocs_mcp.server" not in combined, (
        "standalone watch path must not import the MCP server"
    )
    assert "asyncio.to_thread" not in combined, (
        "standalone watch path must not run anything on to_thread "
        "(only --watch + MCP path does)"
    )


def test_run_watch_only_uses_fakeobserver_without_mcp(
    tmp_path, monkeypatch,
) -> None:
    """End-to-end: ``_run_watch_only`` runs the watcher to completion,
    without ever touching ``pydocs_mcp.server.run``.

    Drives the watch-only loop in a task that gets cancelled after a
    short window so the FakeObserver's ``stop()`` path is exercised.
    """
    from tests._fakes import FakeObserver

    fake_observer = FakeObserver()

    args = argparse.Namespace(
        project=str(tmp_path),
        verbose=False,
        cache_dir=None,
        no_inspect=True,
        config=None,
    )

    from pydocs_mcp.__main__ import _run_watch_only
    from pydocs_mcp.serve import watcher as watcher_mod

    monkeypatch.setattr(
        watcher_mod, "_load_watchdog", lambda: (lambda: fake_observer),
    )

    # Stub server.run so any accidental import path doesn't actually
    # start FastMCP. Test asserts this is never called.
    import pydocs_mcp.server as srv
    server_calls: list[None] = []

    def _trip_wire(*args, **kwargs):  # noqa: ARG001
        server_calls.append(None)

    monkeypatch.setattr(srv, "run", _trip_wire)

    async def _drive() -> None:
        loop_task = asyncio.create_task(_run_watch_only(args))
        await asyncio.sleep(0.01)
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    asyncio.run(_drive())

    assert fake_observer.started is False, (
        "Observer.stop should be called when the task is cancelled"
    )
    assert server_calls == [], (
        "standalone watch must not call pydocs_mcp.server.run"
    )


def test_cmd_watch_registered_in_cmd_table() -> None:
    """``watch`` is dispatched via the main() ``_CMD_TABLE`` mapping —
    pin the wiring so a future refactor that drops the entry breaks here
    instead of producing a silent ``KeyError`` at runtime.
    """
    from pydocs_mcp.__main__ import _CMD_TABLE, _cmd_watch

    assert "watch" in _CMD_TABLE
    assert _CMD_TABLE["watch"] is _cmd_watch
