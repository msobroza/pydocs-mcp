"""#318 end to end on real repositories and real bundles (spec §6.8b; AC-19, AC-20).

- AC 1: with auto-fetch off (O14) the refresh loop of ``serve`` and ``watch``
  spawns no network process — read off every command it really spawned —
  while it still follows a fetch someone else made.
- AC 2: the behind-upstream hint reaches ``meta.suggestion`` from the lane's
  last computed statuses; no tool call spawns a process, and without a behind
  status every answer is byte-identical.
- AC 3 / AC-20: a remote that refuses is logged once; a local commit and an
  uncommitted edit are still indexed, no answer turns stale or hints a sync, and
  the lane recovers by itself when the remote comes back.
- AC-19: with auto-fetch and fast-forward on, a push to the remote's ``main``
  moves local ``main`` and its ``branches`` row; the checked-out branch and
  its working tree are untouched.
- Layer 2: a tracked remote-tracking ref is indexed from git objects, answers
  as a branch, and is never retired.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from pydocs_mcp.__main__ import (
    _build_parser,
    _build_watcher_and_callback,
    _run_indexing,
    _run_refresh_loop,
    _run_watch_only,
)
from pydocs_mcp.application.extra_branch_passes import ExtraBranchRequest
from pydocs_mcp.application.mcp_inputs import GrepInput, SearchInput
from pydocs_mcp.application.tool_response import SUGGESTION_TOOLS
from pydocs_mcp.application.upstream_status import (
    CheckoutPlace,
    UpstreamStatus,
    upstream_status_board_for,
)
from pydocs_mcp.db import cache_path_for_project
from pydocs_mcp.extraction.strategies import embedders as _embedders
from pydocs_mcp.git.refs import locate_gitdir
from pydocs_mcp.models import BranchIndexSource, BranchStatus
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.serve import ref_watcher as ref_watcher_mod
from pydocs_mcp.serve import watcher as watcher_mod
from pydocs_mcp.serve.ref_watcher import RefWatcher
from pydocs_mcp.serve.refresh_wiring import RefreshWiring, build_refresh_parts
from pydocs_mcp.serve.remote_sync import RemoteSyncState
from pydocs_mcp.server import build_routers
from pydocs_mcp.storage.factories import build_project_indexer, build_sqlite_uow_factory
from tests._fakes import FakeObserver, RecordingEmbedder
from tests._git_sandbox import (
    NoProcessSpawned,
    SpawnRecorder,
    commit_text,
    isolate_git_config,
    requires_git,
    run_git,
)
from tests.integration.test_branch_freshness_per_project import _call, _indexed_git_project
from tests.integration.test_branch_selector_identity import _nine_tool_calls
from tests.serve._remote_fakes import logged_events
from tests.serve.test_remote_sync_git import project_with_remote, push_to_main

pytestmark = requires_git

# A bounded wait on work a background task does — never a timing assumption:
# the reconciliation tick below is one second.
_DEADLINE_SECONDS = 20.0
_POLL_SECONDS = 0.05


@pytest.fixture(autouse=True)
def _sandboxed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")
    config = AppConfig.load().embedding
    recording = RecordingEmbedder(dim=config.dim, model_name=config.model_name)
    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: recording)
    monkeypatch.setattr(watcher_mod, "_load_watchdog", lambda: FakeObserver)
    # No inotify: the ref watcher wakes on its reconciliation tick only.
    monkeypatch.setattr(ref_watcher_mod, "_load_ref_observers", lambda: (FakeObserver,))


def _config(tmp_path: Path, yaml: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml, encoding="utf-8")
    return path


def _argv(verb: str, root: Path, config: Path, *flags: str) -> list[str]:
    return ["--config", str(config), verb, str(root), "--no-inspect", "--skip-deps", *flags]


async def _index(root: Path, config: Path, *flags: str) -> Path:
    await _run_indexing(_build_parser().parse_args(_argv("index", root, config, *flags)))
    return cache_path_for_project(root.resolve())


async def _rows(db: Path) -> dict[str, Any]:
    async with build_sqlite_uow_factory(db)() as uow:
        return {r.name: r for r in await uow.branches.list_branches()}


async def _until(condition: Any, describe: Any) -> None:
    deadline = time.monotonic() + _DEADLINE_SECONDS
    while not condition():
        assert time.monotonic() < deadline, describe()
        await asyncio.sleep(_POLL_SECONDS)


# ── AC 1: auto-fetch off spawns no network process, under serve and watch ───


_TRACK_ORIGIN_MAIN = (
    "git:\n  ref_watch: {reconcile_seconds: 1}\n  remote: {track_refs: [origin/main]}\n"
)


def _refresh_loop_of(verb: str, args: Any, db: Path) -> Any:
    """What ``serve`` runs beside its server, or the standalone ``watch``."""
    if verb == "serve":
        return _run_refresh_loop(args, db_path=db, with_file_watcher=False)
    return _run_watch_only(args)


@pytest.mark.parametrize("verb", ["serve", "watch"])
async def test_with_auto_fetch_off_the_refresh_loop_spawns_no_network_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    """Both follow a fetch someone else made — ``origin/main`` (tracked) is
    reindexed at the fetched tip — reading every command they really spawned.
    Only ``serve`` computes the behind-upstream signal: under ``watch`` no
    server in the process could carry it (#318 review)."""
    root, other = project_with_remote(tmp_path)
    run_git(root, "switch", "-q", "main")
    tip = push_to_main(other, count=3)
    run_git(root, "fetch", "-q", "origin")  # the objects; origin/main is then set back
    run_git(root, "update-ref", "refs/remotes/origin/main", f"{tip}~1")
    config = _config(tmp_path, _TRACK_ORIGIN_MAIN)
    db = await _index(root, config)
    board = upstream_status_board_for(db)
    recorder = SpawnRecorder()
    recorder.install(monkeypatch)
    args = _build_parser().parse_args(_argv(verb, root, config))
    loop = asyncio.create_task(_refresh_loop_of(verb, args, db))

    def behind() -> list[int]:
        return [s.behind for s in board.latest() if s.branch == "main"]

    await _until_row_head(db, "origin/main", run_git(root, "rev-parse", f"{tip}~1"))
    if verb == "serve":
        await _until(lambda: behind() == [2], behind)
    # Someone else's fetch (an IDE, a terminal): the remote-tracking ref moves.
    run_git(root, "update-ref", "refs/remotes/origin/main", tip)
    await _until_row_head(db, "origin/main", tip)
    if verb == "serve":
        await _until(lambda: behind() == [3], behind)
    loop.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await loop
    assert recorder.network_commands() == []
    layer_one_ran = any("--left-right" in argv for argv in recorder.commands)
    assert layer_one_ran is (verb == "serve")
    assert (board.latest() == ()) is (verb == "watch")


async def _row_head(db: Path, name: str) -> str | None:
    row = (await _rows(db)).get(name)
    return None if row is None else row.head_sha


async def _until_row_head(db: Path, name: str, sha: str) -> None:
    deadline = time.monotonic() + _DEADLINE_SECONDS
    head = await _row_head(db, name)
    while head != sha:
        assert time.monotonic() < deadline, f"{name} is at {head}, not {sha}"
        await asyncio.sleep(_POLL_SECONDS)
        head = await _row_head(db, name)


# ── AC 2: the hint on the request path, from the published statuses only ───

_HINT = "[suggestion: branch 'main' is behind origin/main by 2; run: git pull]"
# ``main`` is the fixture's checked-out branch: ``git pull`` syncs it.
_MAIN_BEHIND = UpstreamStatus("main", "origin/main", 0, 2, None, CheckoutPlace.THIS_WORKTREE)


def _no_ttl_config(tmp_path: Path, git_yaml: str = "") -> AppConfig:
    overlay = tmp_path / "no-ttl.yaml"
    overlay.write_text(
        f"output:\n  envelope: {{ head_check_ttl_seconds: 0 }}\n{git_yaml}", encoding="utf-8"
    )
    return AppConfig.load(explicit_path=overlay)


def _answers(router: Any) -> dict[str, Any]:
    return {name: _call(router, m, p) for name, (m, p) in _nine_tool_calls().items()}


def test_the_behind_hint_rides_meta_suggestion_and_no_tool_call_spawns_a_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, db = _indexed_git_project(tmp_path, "proj")
    router, _ = build_routers(_no_ttl_config(tmp_path), db_path=db, surface="mcp")
    before = _answers(router)
    upstream_status_board_for(db).publish((_MAIN_BEHIND,))
    monkeypatch.setattr(subprocess, "Popen", NoProcessSpawned)
    after = _answers(router)
    for name, (method, _payload) in _nine_tool_calls().items():
        assert (after[name].text, after[name].items) == (before[name].text, before[name].items)
        hinted = method in SUGGESTION_TOOLS and "suggestion" not in before[name].meta
        extra = {"suggestion": _HINT} if hinted else {}
        assert after[name].meta == {**before[name].meta, **extra}, name


@pytest.mark.parametrize(
    "switch_off",
    [
        "git:\n  remote: { behind_hint: false }\n",  # the layer's own switch
        "  suggestions: { behind_upstream: false }\n",  # the ADR 0007 rule flag
    ],
)
def test_with_the_behind_hint_off_every_answer_stays_byte_identical(
    tmp_path: Path, switch_off: str
) -> None:
    _root, db = _indexed_git_project(tmp_path, "proj")
    config = _no_ttl_config(tmp_path, switch_off)
    router, _ = build_routers(config, db_path=db, surface="mcp")
    before = _answers(router)
    upstream_status_board_for(db).publish((_MAIN_BEHIND,))
    after = _answers(router)
    for name in before:
        assert (after[name].text, after[name].items, after[name].meta) == (
            before[name].text,
            before[name].items,
            before[name].meta,
        ), name


# ── The production queue, runner and lane, fed by ref-snapshot diffs ───────


class RefreshChain:
    """``build_refresh_parts`` — the queue, its runner and the remote lane that
    ``serve`` / ``watch`` run — driven step by step: the test checks the remote,
    then hands the ref diff to the submissions as the watcher would."""

    def __init__(self, root: Path, config_path: Path) -> None:
        self.config = AppConfig.load(explicit_path=config_path)
        self.db = cache_path_for_project(root.resolve())
        args = _build_parser().parse_args(_argv("watch", root, config_path))
        _watcher, reindex = _build_watcher_and_callback(args, self.config.serve.watch)
        wiring = RefreshWiring(
            config=self.config,
            project_root=root.resolve(),
            db_path=self.db,
            reindex_working_tree=reindex,
            bundle_factory=lambda: build_project_indexer(
                self.config, self.db, use_inspect=False, inspect_depth=None
            ),
            extra_branches=ExtraBranchRequest(),
        )
        gitdir = locate_gitdir(root)
        assert gitdir is not None
        self.parts = build_refresh_parts(wiring, gitdir)
        assert self.parts.remote_lane is not None
        self.lane = self.parts.remote_lane
        git = self.config.git
        self.watcher = RefWatcher(
            gitdir,
            git.branches.base,
            git.remote.name,
            git.ref_watch.debounce_ms,
            git.ref_watch.reconcile_seconds,
            observer_factories=(FakeObserver,),
        )
        self.previous = self.watcher.snapshot()

    async def __aenter__(self) -> RefreshChain:
        self.drain = asyncio.create_task(self.parts.submissions.queue.run_until_cancelled())
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.drain.cancel()
        with pytest.raises(asyncio.CancelledError):
            await self.drain

    async def settle(self) -> None:
        """What the watcher's next wake-up hands the queue, then drain it."""
        current = self.watcher.snapshot()
        events = self.watcher.diff(self.previous, current)
        self.previous = current
        await self.parts.submissions.on_ref_events(events)
        await self.parts.submissions.queue.wait_idle()


_LANES_ON = """git:
  branches: {track: [checked_out, main]}
  remote:
    auto_fetch: {enabled: true}
    fast_forward_branches_without_worktree: true
"""


async def test_a_push_to_the_remote_main_moves_local_main_and_its_row_only(
    tmp_path: Path,
) -> None:
    """AC-19: ``main`` is tracked and checked out nowhere; ``feature/x`` is."""
    root, other = project_with_remote(tmp_path)
    config = _config(tmp_path, _LANES_ON)
    db = await _index(root, config, "--branch", "main")
    before = await _rows(db)
    tip = push_to_main(other)
    async with RefreshChain(root, config) as chain:
        await chain.lane.check_remote_once()
        await chain.settle()
    rows = await _rows(db)
    assert (rows["main"].head_sha, rows["main"].source) == (tip, BranchIndexSource.GIT_OBJECTS)
    assert rows["feature/x"] == before["feature/x"]
    assert run_git(root, "symbolic-ref", "--short", "HEAD") == "feature/x"
    assert run_git(root, "status", "--porcelain") == "" and not (root / "landed_0.py").exists()


async def _project_texts(db: Path) -> str:
    async with build_sqlite_uow_factory(db)() as uow:
        return "\n".join(c.text for c in await uow.chunks.list({"package": "__project__"}))


async def test_a_refusing_remote_leaves_every_local_lane_working_and_recovers_by_itself(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """AC 3 / AC-20: while the lane is offline a local commit and an
    uncommitted edit are still indexed, it is logged once, no answer carries a
    stale warning or a sync hint because of the outage — and when the remote
    comes back the next check recovers, with no restart."""
    root, _other = project_with_remote(tmp_path)
    bare = run_git(root, "remote", "get-url", "origin")
    run_git(root, "remote", "set-url", "origin", (tmp_path / "nowhere.git").as_uri())
    config = _config(tmp_path, _LANES_ON)
    db = await _index(root, config)
    async with RefreshChain(root, config) as chain:
        with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
            await chain.lane.check_remote_once()
            head = commit_text(root, "app.py", "def run() -> int:\n    return 2\n", "local")
            await chain.settle()
            (root / "offline_edit.py").write_text("def offline_edit() -> int:\n    return 7\n")
            await chain.parts.submissions.on_file_change()  # what the file watcher hands over
            await chain.parts.submissions.queue.wait_idle()
            await chain.lane.check_remote_once()
            router, _ = build_routers(chain.config, db_path=db, surface="mcp")
            answer = await router.search_codebase(SearchInput(query="offline edit"))
            run_git(root, "remote", "set-url", "origin", bare)  # the network is back
            await chain.lane.check_remote_once()
    assert (await _rows(db))["feature/x"].head_sha == head
    assert "def offline_edit" in await _project_texts(db)
    assert answer.meta["index_stale"] is False and "index stale" not in answer.text
    assert "suggestion" not in answer.meta or "git " not in answer.meta["suggestion"]
    offline = logged_events(caplog, "remote_sync_offline")
    online = logged_events(caplog, "remote_sync_online")
    assert (len(offline), len(online)) == (1, 1)
    assert chain.lane.state is RemoteSyncState.ONLINE


async def test_a_tracked_remote_ref_is_indexed_answers_as_a_branch_and_is_never_retired(
    tmp_path: Path,
) -> None:
    """Layer 2 with auto-fetch off: ``track_refs: [origin/main]``. A fetch by
    anyone moves it (the base tip too): its pass runs from git objects, the
    merge-base re-check's maintenance leaves it ACTIVE, and ``branch=
    "origin/main"`` answers from the remote's state."""
    root, other = project_with_remote(tmp_path)
    run_git(root, "switch", "-q", "main")
    config = _config(tmp_path, "git:\n  remote: {track_refs: [origin/main]}\n")
    db = await _index(root, config)
    tip = push_to_main(other)
    async with RefreshChain(root, config) as chain:
        run_git(root, "fetch", "-q", "origin")  # the user's fetch, not the lane's
        await chain.settle()
    row = (await _rows(db))["origin/main"]
    assert (row.head_sha, row.source, row.status) == (
        tip,
        BranchIndexSource.GIT_OBJECTS,
        BranchStatus.ACTIVE,
    )
    router, _ = build_routers(AppConfig.load(explicit_path=config), db_path=db, surface="mcp")
    grep = {"pattern": "def landed_0", "output_mode": "files_with_matches"}
    on_remote = await router.grep(GrepInput(**grep, branch="origin/main"))
    on_local = await router.grep(GrepInput(**grep))
    assert on_remote.meta["branch"] == "origin/main" and "landed_0.py" in on_remote.text
    assert on_local.meta["branch"] == "main" and "landed_0.py" not in on_local.text
