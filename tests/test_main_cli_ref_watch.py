"""Ref-driven refresh is on by default under ``serve`` and ``watch`` (spec §6.8,
#317): plain ``serve`` in a repository runs the refresh loop on its own thread
while the MCP server keeps the main thread; ``git.ref_watch.enabled: false``, a
non-git project or a workspace load serve exactly as before."""

from __future__ import annotations

import argparse
import asyncio
import threading
from pathlib import Path

import pytest

import pydocs_mcp.__main__ as main_mod
from tests._git_sandbox import isolate_git_config, requires_git, run_git

pytestmark = requires_git


@pytest.fixture(autouse=True)
def _sandboxed_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    return root


@pytest.fixture
def serve_seams(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Patch ``_cmd_serve``'s phases; return (serve, calls)."""
    calls: list[tuple[str, bool]] = []
    refresh_cancelled = threading.Event()
    monkeypatch.setattr(main_mod, "_run_cmd", lambda coro, verbose: (coro.close(), 0)[1])

    def plain(args, db_path, workspace, db_paths) -> int:
        calls.append(("server", threading.current_thread() is threading.main_thread()))
        return 0

    async def refresh(args, *, db_path, with_file_watcher, serve=None) -> None:
        calls.append(("refresh", with_file_watcher))
        try:
            await asyncio.Event().wait()
        finally:
            refresh_cancelled.set()

    monkeypatch.setattr(main_mod, "_serve_run", plain)
    monkeypatch.setattr(main_mod, "_run_refresh_loop", refresh)

    def serve(project: Path, yaml: str = "") -> list[tuple[str, bool]]:
        overlay = tmp_path / "overlay.yaml"
        overlay.write_text(yaml, encoding="utf-8")
        args = argparse.Namespace(
            project=str(project),
            cache_dir=str(tmp_path / "cache"),
            watch=False,
            config=overlay,
            verbose=False,
            workspace=None,
            db_paths=None,
        )
        assert main_mod._cmd_serve(args) == 0
        # Membership, not order: the refresh thread signals it is ready before
        # its coroutine runs, so the server may record its call first.
        if ("refresh", False) in calls:
            assert refresh_cancelled.is_set(), "the refresh loop outlived the server"
        return calls

    return serve


def test_plain_serve_in_a_repository_refreshes_beside_the_main_thread_server(
    tmp_path: Path, serve_seams
) -> None:
    calls = serve_seams(_repo(tmp_path))
    # The MCP server kept the main thread; the refresh ran with the ref watcher
    # only (no --watch), and each ran once.
    assert sorted(calls) == [("refresh", False), ("server", True)]


def test_ref_watch_disabled_serves_exactly_as_before(tmp_path: Path, serve_seams) -> None:
    calls = serve_seams(_repo(tmp_path), "git:\n  ref_watch:\n    enabled: false\n")
    assert calls == [("server", True)]


def test_a_project_that_is_not_a_repository_serves_exactly_as_before(
    tmp_path: Path, serve_seams
) -> None:
    project = tmp_path / "plain"
    project.mkdir()
    assert serve_seams(project) == [("server", True)]


def test_git_off_serves_exactly_as_before(tmp_path: Path, serve_seams) -> None:
    assert serve_seams(_repo(tmp_path), 'git:\n  enabled: "off"\n') == [("server", True)]


async def test_watch_mode_runs_the_server_inside_the_refresh_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``serve --watch``: one loop holds the queue, both watchers and the server."""
    seen: dict[str, object] = {}

    async def refresh(args, *, db_path, with_file_watcher, serve=None) -> None:
        seen.update(db_path=db_path, with_file_watcher=with_file_watcher)
        await serve()

    served: list[Path] = []
    monkeypatch.setattr(main_mod, "_run_refresh_loop", refresh)
    monkeypatch.setattr("pydocs_mcp.server.run", lambda db, **kwargs: served.append(db))
    args = argparse.Namespace(project=str(tmp_path), config=None, gpu=False, descriptions=None)
    await main_mod._run_watch_loop(args, db_path=tmp_path / "x.db")
    assert seen == {"db_path": tmp_path / "x.db", "with_file_watcher": True}
    assert served == [tmp_path / "x.db"]


async def test_standalone_watch_runs_the_refresh_loop_without_a_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[bool, object, bool]] = []

    async def refresh(
        args, *, db_path, with_file_watcher, serve=None, answers_requests=True
    ) -> None:
        seen.append((with_file_watcher, serve, answers_requests))

    monkeypatch.setattr(main_mod, "_run_refresh_loop", refresh)
    args = argparse.Namespace(project=str(tmp_path), cache_dir=None, config=None)
    await main_mod._run_watch_only(args)
    # #318: no server in the process, so nothing computes the behind-upstream signal.
    assert seen == [(True, None, False)]
