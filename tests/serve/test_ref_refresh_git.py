"""#317's acceptance criteria on real git repositories: the ref watcher reads the
refs git itself wrote, the debounce runs on a manual clock, and the queue's
runner only records — which jobs, how many, which pass each one runs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.application.served_branch_head import ServedBranchHead
from pydocs_mcp.git.refs import locate_gitdir
from pydocs_mcp.retrieval.config.git_models import GitBranchesConfig
from pydocs_mcp.serve.index_job_runner import IndexJobRunner
from pydocs_mcp.serve.index_jobs import IndexJob, IndexJobKind, IndexJobQueue
from pydocs_mcp.serve.ref_watcher import RefEventKind, RefEvents, RefWatcher
from pydocs_mcp.serve.refresh_jobs import BranchTracking
from pydocs_mcp.serve.refresh_loop import RefreshSubmissions
from tests._git_sandbox import commit_text, isolate_git_config, requires_git, run_git
from tests.serve._ref_watch_fakes import ManualTimer, RoutingFakeObserver

pytestmark = requires_git

BRANCH, RECHECK = IndexJobKind.BRANCH_INDEX, IndexJobKind.MERGE_BASE_RECHECK
DEBOUNCE_S = 1.0


@pytest.fixture(autouse=True)
def _sandboxed_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit_text(root, "app.py", "x = 1\n", "init")
    run_git(root, "branch", "feature/x")
    return root


def _checked_out(root: Path) -> ServedBranchHead:
    """What the working-tree pass stamps for the checkout as it is now."""
    return ServedBranchHead(
        run_git(root, "symbolic-ref", "--short", "HEAD"), run_git(root, "rev-parse", "HEAD")
    )


def _diffs_only(
    on_events: Callable[[RefEvents], Awaitable[None]],
) -> Callable[[RefEvents], Awaitable[None]]:
    """Drop the watch's start-up report: these tests count what the diffs queue
    (the start-up report has tests of its own below)."""

    async def forward(events: RefEvents) -> None:
        diffs = tuple(e for e in events if e.kind is not RefEventKind.HEAD_AT_START)
        if diffs:
            await on_events(diffs)

    return forward


@dataclass
class Refresh:
    """A real ``RefWatcher`` feeding a queue through the production submissions;
    the queue is not drained until the test says so."""

    gitdir: Path
    observer: RoutingFakeObserver
    timer: ManualTimer
    queue: IndexJobQueue
    submissions: RefreshSubmissions
    watch: asyncio.Task[None]

    def jobs(self) -> list[tuple[IndexJobKind, str, int]]:
        return [(j.kind, j.branch, j.priority) for j in self.queue.snapshot()]

    async def settle(self, *changed: Path) -> None:
        """Report ``changed`` to the watcher, then let its debounce run out: each
        wake-up starts one debounce sleep, the delivery ends on the tick's."""
        before = self.timer.sleeps_started
        for path in changed:
            self.observer.fire(str(path))
        await self.timer.wait_for_sleeps(before + len(changed))
        self.timer.advance(DEBOUNCE_S)
        await self.timer.wait_for_sleeps(before + len(changed) + 1)

    async def drain(self) -> None:
        worker = asyncio.create_task(self.queue.run_until_cancelled())
        await self.queue.wait_idle()
        worker.cancel()
        self.watch.cancel()


async def _refresh(
    root: Path,
    runner,
    *,
    track: tuple[str, ...] = ("checked_out",),
    with_start_report: bool = False,
) -> Refresh:
    gitdir = locate_gitdir(root)
    assert gitdir is not None
    queue = IndexJobQueue(runner)
    submissions = RefreshSubmissions(
        queue, BranchTracking(gitdir, GitBranchesConfig(track=list(track)))
    )
    observer, timer = RoutingFakeObserver(), ManualTimer()
    watcher = RefWatcher(
        gitdir,
        "auto",
        "origin",
        int(DEBOUNCE_S * 1000),
        60,
        observer_factories=(lambda: observer,),
        sleep=timer.sleep,
    )
    on_events = submissions.on_ref_events
    watch = asyncio.create_task(
        watcher.run_until_cancelled(on_events if with_start_report else _diffs_only(on_events))
    )
    await timer.wait_for_sleeps(1)  # the baseline snapshot is taken
    return Refresh(watcher.gitdir, observer, timer, queue, submissions, watch)


class Recorder:
    """The production runner's seams, recorded. A working-tree pass stamps the
    served row from the checkout it read when it started, like the real pass;
    ``hold`` makes the next one wait for ``release`` once it has started."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.jobs: list[IndexJob] = []
        self.passes: list[tuple[str, ...]] = []
        # The startup pass indexed the checkout the repository starts on.
        self.served: ServedBranchHead | None = _checked_out(root)
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.release.set()

    def hold(self) -> None:
        self.release.clear()

    async def job(self, job: IndexJob) -> None:
        self.jobs.append(job)

    async def working_tree(self) -> None:
        read = _checked_out(self.root)
        self.passes.append(("working_tree", read.name))
        self.started.set()
        await self.release.wait()
        self.served = read

    async def served_head(self) -> ServedBranchHead | None:
        return self.served

    async def git_objects(self, name: str) -> None:
        self.passes.append(("git_objects", name))

    async def recheck(self) -> None:
        self.passes.append(("recheck",))

    def runner(self, track: tuple[str, ...] = ("checked_out",)) -> IndexJobRunner:
        return IndexJobRunner(
            tracking=BranchTracking(locate_gitdir(self.root), GitBranchesConfig(track=list(track))),
            reindex_working_tree=self.working_tree,
            index_other_branch=self.git_objects,
            recheck_merge_bases=self.recheck,
            served_head=self.served_head,
        )


async def test_checking_out_a_tracked_branch_queues_exactly_one_pass(tmp_path: Path) -> None:
    """AC 1. The checkout rewrote files too, so the file watcher's job arrives
    beside the ref watcher's: both are keyed on the branch, one pass runs."""
    root = _repo(tmp_path)
    recorder = Recorder(root)
    refresh = await _refresh(root, recorder.job)
    run_git(root, "switch", "-q", "feature/x")
    await refresh.submissions.on_file_change()
    await refresh.settle(refresh.gitdir / "HEAD", refresh.gitdir / "logs" / "HEAD")
    assert refresh.jobs() == [(BRANCH, "feature/x", 0)]
    await refresh.drain()
    assert [(j.kind, j.branch) for j in recorder.jobs] == [(BRANCH, "feature/x")]


async def test_a_checkout_whose_file_job_is_already_running_still_runs_one_pass(
    tmp_path: Path,
) -> None:
    """AC 1 under ``serve --watch`` timing: the file watcher's quiet period
    (``serve.watch.debounce_ms``, 500) ends before the ref watcher's (1000), so
    the checkout's file job is already running when ``HEAD_MOVED`` arrives. The
    ref job parks behind it and is dropped once that pass has stamped the branch
    at the head the ref watcher saw."""
    root = _repo(tmp_path)
    recorder = Recorder(root)
    refresh = await _refresh(root, recorder.runner())
    worker = asyncio.create_task(refresh.queue.run_until_cancelled())
    run_git(root, "switch", "-q", "feature/x")
    recorder.hold()
    await refresh.submissions.on_file_change()
    await recorder.started.wait()  # the file job's pass is running
    await refresh.settle(refresh.gitdir / "HEAD", refresh.gitdir / "logs" / "HEAD")
    assert refresh.jobs() == []  # parked behind the running pass, not pending
    recorder.release.set()
    await refresh.queue.wait_idle()
    worker.cancel()
    refresh.watch.cancel()
    assert recorder.passes == [("working_tree", "feature/x")]


async def test_a_save_during_that_pass_still_runs_its_own_pass(tmp_path: Path) -> None:
    """The parked ref job absorbs a save made while the pass ran: that save may
    hold an edit the running pass never read, so the follow-up runs."""
    root = _repo(tmp_path)
    recorder = Recorder(root)
    refresh = await _refresh(root, recorder.runner())
    worker = asyncio.create_task(refresh.queue.run_until_cancelled())
    run_git(root, "switch", "-q", "feature/x")
    recorder.hold()
    await refresh.submissions.on_file_change()
    await recorder.started.wait()
    await refresh.settle(refresh.gitdir / "HEAD")
    await refresh.submissions.on_file_change()
    recorder.release.set()
    await refresh.queue.wait_idle()
    worker.cancel()
    refresh.watch.cancel()
    assert recorder.passes == [("working_tree", "feature/x")] * 2


async def test_a_checkout_during_the_startup_pass_queues_exactly_one_pass(tmp_path: Path) -> None:
    """The startup pass stamped ``main``; the checkout landed before the watch
    took its first snapshot, so no diff reports it. The start-up report does:
    one pass for the branch now checked out."""
    root = _repo(tmp_path)
    recorder = Recorder(root)  # the startup pass indexed main
    run_git(root, "switch", "-q", "feature/x")
    refresh = await _refresh(root, recorder.runner(), with_start_report=True)
    assert refresh.jobs() == [(BRANCH, "feature/x", 0)]
    await refresh.drain()
    assert recorder.passes == [("working_tree", "feature/x")]


async def test_a_watch_that_starts_on_the_indexed_head_runs_no_pass(tmp_path: Path) -> None:
    """Every start of a single-branch bundle: the start-up report names the
    head the startup pass just indexed, and the runner drops it."""
    root = _repo(tmp_path)
    recorder = Recorder(root)
    refresh = await _refresh(root, recorder.runner(), with_start_report=True)
    await refresh.drain()
    assert recorder.passes == []


async def test_a_commit_on_the_current_branch_refreshes_that_branch_only(tmp_path: Path) -> None:
    """AC 2, through the production runner: the working-tree pass, once — no
    other branch, no re-check (the base did not move)."""
    root = _repo(tmp_path)
    run_git(root, "switch", "-q", "feature/x")
    recorder = Recorder(root)
    refresh = await _refresh(root, recorder.runner(("all_local",)), track=("all_local",))
    commit_text(root, "app.py", "x = 2\n", "edit")
    await refresh.settle(refresh.gitdir / "refs" / "heads" / "feature" / "x")
    assert refresh.jobs() == [(BRANCH, "feature/x", 0)]
    await refresh.drain()
    assert recorder.passes == [("working_tree", "feature/x")]


async def test_two_commits_in_the_debounce_window_coalesce_into_one_pass(tmp_path: Path) -> None:
    """AC 3: the quiet period restarts on the second commit; one diff, one job."""
    root = _repo(tmp_path)
    recorder = Recorder(root)
    refresh = await _refresh(root, recorder.job)
    ref = refresh.gitdir / "refs" / "heads" / "main"
    commit_text(root, "app.py", "x = 2\n", "first")
    refresh.observer.fire(str(ref))
    await refresh.timer.wait_for_sleeps(2)
    refresh.timer.advance(DEBOUNCE_S / 2)
    commit_text(root, "app.py", "x = 3\n", "second")
    refresh.observer.fire(str(ref))
    await refresh.timer.wait_for_sleeps(3)
    refresh.timer.advance(DEBOUNCE_S / 2)  # a window after the first, half after the second
    assert refresh.jobs() == [] and refresh.timer.sleeps_started == 3
    refresh.timer.advance(DEBOUNCE_S / 2)
    await refresh.timer.wait_for_sleeps(4)
    # ``main`` is the base, its local ref the tip: the commit also re-checks.
    assert refresh.jobs() == [(BRANCH, "main", 0), (RECHECK, "", 3)]
    await refresh.drain()
    assert [j.branch for j in recorder.jobs if j.kind is BRANCH] == ["main"]


async def test_a_branch_the_refresh_does_not_track_queues_nothing(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    refresh = await _refresh(root, Recorder(root).job)
    # Move feature/x, and leave main (checked out, the base) where it was.
    commit_text(root, "app.py", "x = 2\n", "edit")
    run_git(root, "branch", "-f", "feature/x", "HEAD")
    run_git(root, "reset", "-q", "--hard", "HEAD~1")
    await refresh.settle(refresh.gitdir / "refs" / "heads" / "feature" / "x")
    assert refresh.jobs() == []
    refresh.watch.cancel()


def _remote_with_clone(tmp_path: Path, root: Path) -> Path:
    bare = tmp_path / "remote.git"
    run_git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    run_git(root, "remote", "add", "origin", str(bare))
    run_git(root, "push", "-q", "origin", "main")
    other = tmp_path / "other"
    run_git(tmp_path, "clone", "-q", str(bare), str(other))
    return other


async def test_a_fetch_re_checks_only_when_it_moves_the_base_tip(tmp_path: Path) -> None:
    """AC-7 / the ticket's base-tip clause: ``git fetch`` reindexes nothing; one
    that moves ``origin/main`` (the base tip) queues the merge-base re-check alone."""
    root = _repo(tmp_path)
    other = _remote_with_clone(tmp_path, root)
    refresh = await _refresh(root, Recorder(root).job)
    remote_refs = refresh.gitdir / "refs" / "remotes" / "origin"
    run_git(other, "switch", "-q", "-c", "topic")
    commit_text(other, "topic.py", "t = 1\n", "topic")
    run_git(other, "push", "-q", "origin", "topic")
    run_git(root, "fetch", "-q", "origin")
    await refresh.settle(remote_refs / "topic")
    assert refresh.jobs() == []
    _land_on_the_remote_base(other)
    run_git(root, "fetch", "-q", "origin")
    await refresh.settle(remote_refs / "main")
    assert refresh.jobs() == [(RECHECK, "", 3)]
    refresh.watch.cancel()


def _land_on_the_remote_base(other: Path) -> None:
    run_git(other, "switch", "-q", "main")
    commit_text(other, "main.py", "m = 1\n", "landed")
    run_git(other, "push", "-q", "origin", "main")


async def test_a_remote_added_after_the_watch_started_becomes_the_base_tip(
    tmp_path: Path,
) -> None:
    """The base tip is chosen on every snapshot, not frozen at start: the first
    push that creates ``origin/main`` hands it the base tip (same commit, no
    re-check), and a later fetch that moves it re-checks the merge-bases."""
    root = _repo(tmp_path)
    refresh = await _refresh(root, Recorder(root).job)  # no remote yet: local main is the tip
    other = _remote_with_clone(tmp_path, root)
    remote_refs = refresh.gitdir / "refs" / "remotes" / "origin"
    await refresh.settle(remote_refs / "main")
    assert refresh.jobs() == []
    _land_on_the_remote_base(other)
    run_git(root, "fetch", "-q", "origin")
    await refresh.settle(remote_refs / "main")
    assert refresh.jobs() == [(RECHECK, "", 3)]
    refresh.watch.cancel()
