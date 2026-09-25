"""#317 end to end on a real repository and a real bundle: ``pydocs-mcp index``
builds the index, then the production job runner (``build_index_job_runner``)
drains what a real ``RefWatcher`` saw — a commit on the checked-out branch, a
move of another tracked branch, a checkout — and the merge-base re-check
re-stamps a drifted base on the real ``branches`` table (spec §6.5, §6.8; AC-7)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_mcp.__main__ import _build_parser, _build_watcher_and_callback, _run_indexing
from pydocs_mcp.application.extra_branch_passes import ExtraBranchRequest
from pydocs_mcp.db import cache_path_for_project
from pydocs_mcp.extraction.strategies import embedders as _embedders
from pydocs_mcp.git.refs import locate_gitdir
from pydocs_mcp.models import BranchIndexSource
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.serve import watcher as watcher_mod
from pydocs_mcp.serve.index_jobs import IndexJobQueue
from pydocs_mcp.serve.ref_watcher import RefWatcher
from pydocs_mcp.serve.refresh_jobs import BranchTracking
from pydocs_mcp.serve.refresh_loop import RefreshSubmissions, run_refresh_loop
from pydocs_mcp.serve.refresh_wiring import (
    RefreshWiring,
    build_index_job_runner,
    build_merge_base_recheck,
)
from pydocs_mcp.storage.factories import build_project_indexer, build_sqlite_uow_factory
from tests._fakes import FakeObserver, RecordingEmbedder
from tests._git_sandbox import commit_text, isolate_git_config, requires_git, run_git
from tests.serve._ref_watch_fakes import ManualTimer, RoutingFakeObserver

pytestmark = requires_git

_TRACK_FEATURES = 'git:\n  branches:\n    track: [checked_out, "feature/*"]\n'


@pytest.fixture(autouse=True)
def _sandboxed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")
    config = AppConfig.load().embedding
    recording = RecordingEmbedder(dim=config.dim, model_name=config.model_name)
    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: recording)
    monkeypatch.setattr(watcher_mod, "_load_watchdog", lambda: FakeObserver)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "core.py").write_text('def run() -> int:\n    """Run."""\n    return 1\n')
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "init")
    run_git(root, "switch", "-q", "-c", "feature/x")
    commit_text(root, "app/extra.py", 'def more() -> int:\n    """More."""\n    return 2\n', "x")
    run_git(root, "switch", "-q", "main")
    return root


def _argv(verb: str, root: Path, config: Path, *flags: str) -> list[str]:
    return ["--config", str(config), verb, str(root), "--no-inspect", "--skip-deps", *flags]


async def _index(root: Path, config: Path, *flags: str) -> None:
    """``pydocs-mcp index`` without its ``asyncio.run`` (the test owns the loop)."""
    await _run_indexing(_build_parser().parse_args(_argv("index", root, config, *flags)))


async def _branches(db: Path) -> dict[str, tuple[str, BranchIndexSource, bool]]:
    async with build_sqlite_uow_factory(db)() as uow:
        rows = await uow.branches.list_branches()
    return {r.name: (r.head_sha, r.source, r.is_default) for r in rows}


class Loop:
    """The refresh loop's production runner and submissions, fed by a real
    ``RefWatcher`` whose observer and clock the test drives."""

    def __init__(self, root: Path, config_path: Path) -> None:
        self.config = AppConfig.load(explicit_path=config_path)
        self.db = cache_path_for_project(root.resolve())
        args = _build_parser().parse_args(_argv("watch", root, config_path))
        _watcher, reindex = _build_watcher_and_callback(args, self.config.serve.watch)
        self.working_tree_passes = 0

        async def counted_reindex() -> None:
            self.working_tree_passes += 1
            await reindex()

        wiring = RefreshWiring(
            config=self.config,
            project_root=root.resolve(),
            db_path=self.db,
            reindex_working_tree=counted_reindex,
            bundle_factory=lambda: build_project_indexer(
                self.config, self.db, use_inspect=False, inspect_depth=None
            ),
            extra_branches=ExtraBranchRequest(),
        )
        self.gitdir = locate_gitdir(root)
        tracking = BranchTracking.for_run(
            self.gitdir, self.config.git.branches, ExtraBranchRequest()
        )
        self.queue = IndexJobQueue(build_index_job_runner(wiring, tracking))
        self.submissions = RefreshSubmissions(self.queue, tracking)
        self.observer, self.timer = RoutingFakeObserver(), ManualTimer()
        self.watcher = RefWatcher(
            self.gitdir,
            self.config.git.branches.base,
            self.config.git.remote.name,
            1000,
            60,
            observer_factories=(lambda: self.observer,),
            sleep=self.timer.sleep,
        )

    async def __aenter__(self) -> Loop:
        self.task = asyncio.create_task(
            run_refresh_loop(self.submissions, ref_watcher=self.watcher, file_watcher=None)
        )
        await self.timer.wait_for_sleeps(1)
        # The start-up report of the head the index pass just stamped: dropped.
        await self.queue.wait_idle()
        assert self.working_tree_passes == 0
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await self.task

    async def refreshed(self, changed: Path) -> None:
        """Report one plumbing change, let the debounce run out, drain the queue."""
        before = self.timer.sleeps_started
        self.observer.fire(str(changed))
        await self.timer.wait_for_sleeps(before + 1)
        self.timer.advance(1.0)
        await self.timer.wait_for_sleeps(before + 2)
        await self.queue.wait_idle()


async def test_commits_branch_moves_and_checkouts_refresh_their_own_branch(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    config = tmp_path / "track.yaml"
    config.write_text(_TRACK_FEATURES, encoding="utf-8")
    await _index(root, config)
    async with Loop(root, config) as loop:
        heads = loop.gitdir / "refs" / "heads"
        # A commit on the checked-out branch: its working-tree pass, nothing else.
        commit_text(
            root, "app/core.py", 'def run() -> int:\n    """Run."""\n    return 3\n', "edit"
        )
        await loop.refreshed(heads / "main")
        main_head = run_git(root, "rev-parse", "main")
        assert (await _branches(loop.db))["main"] == (
            main_head,
            BranchIndexSource.WORKING_TREE,
            True,
        )
        assert loop.working_tree_passes == 1
        # A tracked branch that is not checked out moves: a git-objects pass.
        run_git(root, "branch", "-f", "feature/x", "main")
        await loop.refreshed(heads / "feature" / "x")
        assert (await _branches(loop.db))["feature/x"] == (
            main_head,
            BranchIndexSource.GIT_OBJECTS,
            False,
        )
        assert loop.working_tree_passes == 1
        # A checkout: exactly one pass, the new working-tree branch's.
        run_git(root, "switch", "-q", "feature/x")
        await loop.refreshed(loop.gitdir / "HEAD")
        rows = await _branches(loop.db)
        assert rows["feature/x"] == (main_head, BranchIndexSource.WORKING_TREE, True)
        assert loop.working_tree_passes == 2


async def test_a_checkout_both_watchers_saw_runs_one_working_tree_pass(tmp_path: Path) -> None:
    """AC 1 under ``serve --watch`` on a real bundle: the file watcher's job for
    the checkout runs first (its quiet period is the shorter one), then the ref
    watcher's ``HEAD_MOVED`` for the same branch and head is dropped against the
    row that pass stamped — whether it arrived mid-pass or after."""
    root = _project(tmp_path)
    config = tmp_path / "plain.yaml"
    config.write_text("", encoding="utf-8")
    await _index(root, config)
    async with Loop(root, config) as loop:
        run_git(root, "switch", "-q", "feature/x")
        await loop.submissions.on_file_change()
        await loop.refreshed(loop.gitdir / "HEAD")
        assert loop.working_tree_passes == 1
        rows = await _branches(loop.db)
        head = run_git(root, "rev-parse", "HEAD")
        assert rows["feature/x"] == (head, BranchIndexSource.WORKING_TREE, True)


async def test_checking_out_a_branch_indexed_from_git_objects_serves_it(tmp_path: Path) -> None:
    """The package-level skip must not take a git-objects row (#310's pass, or
    one the ref watcher queued) for the working tree's own stamp: when the
    checkout rewrites no file the package hash matches, and a skip would leave
    the previous branch served while HEAD names the new one."""
    root = _project(tmp_path)
    run_git(root, "branch", "feature/y", "main")  # same tree: the checkout rewrites nothing
    config = tmp_path / "plain.yaml"
    config.write_text("", encoding="utf-8")
    await _index(root, config, "--branch", "feature/y")
    db = cache_path_for_project(root.resolve())
    assert (await _branches(db))["feature/y"][1:] == (BranchIndexSource.GIT_OBJECTS, False)
    run_git(root, "switch", "-q", "feature/y")
    await _index(root, config)
    rows = await _branches(db)
    assert rows["feature/y"][1:] == (BranchIndexSource.WORKING_TREE, True)
    assert "main" not in rows  # the previous checkout is retired, as on any switch


async def test_the_recheck_re_stamps_a_drifted_base_on_the_real_bundle(tmp_path: Path) -> None:
    """The repair #308 left to this job: a stamp that no longer matches the
    resolved base and merge-base is rewritten; a right one is left alone."""
    root = _project(tmp_path)
    config = tmp_path / "plain.yaml"
    config.write_text("", encoding="utf-8")
    await _index(root, config, "--branch", "feature/x")
    db = cache_path_for_project(root.resolve())
    factory = build_sqlite_uow_factory(db)
    async with factory() as uow:
        drifted = await uow.branches.get_branch("feature/x")
        await uow.branches.upsert_branch(
            replace(drifted, base_name="trunk", merge_base_sha="0" * 40)
        )
        await uow.commit()
    await build_merge_base_recheck(AppConfig.load(explicit_path=config), db, root).run()
    async with factory() as uow:
        restamped = await uow.branches.get_branch("feature/x")
        main = await uow.branches.get_branch("main")
    assert (restamped.base_name, restamped.merge_base_sha) == (
        "main",
        run_git(root, "rev-parse", "main"),
    )
    assert (main.base_name, main.merge_base_sha) == ("main", run_git(root, "rev-parse", "main"))
