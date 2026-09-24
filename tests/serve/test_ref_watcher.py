"""RefWatcher (spec §6.8, #317): plumbing snapshots, the diff, the wake-up filter,
the quiet-period debounce and the reconciliation tick — driven by a manual timer,
never by sleeping."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from pydocs_mcp.models import NON_GIT_BRANCH_NAME
from pydocs_mcp.serve.ref_watcher import (
    RefEvent,
    RefEventKind,
    RefWatcher,
    head_branch_name,
)
from tests.serve._ref_watch_fakes import ManualTimer, RoutingFakeObserver

A, B, C = "a" * 40, "b" * 40, "c" * 40
DEBOUNCE_MS, RECONCILE_S = 1000, 60


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _gitdir(tmp_path: Path) -> Path:
    gitdir = tmp_path / ".git"
    for sub in ("refs/heads/feature", "refs/tags", "refs/remotes/origin", "logs", "objects"):
        (gitdir / sub).mkdir(parents=True)
    _write(gitdir / "HEAD", "ref: refs/heads/main\n")
    _write(gitdir / "refs" / "heads" / "main", A + "\n")
    _write(gitdir / "refs" / "heads" / "feature" / "x", B + "\n")
    _write(gitdir / "packed-refs", f"# pack-refs\n{C} refs/remotes/origin/main\n")
    return gitdir.resolve()


def _watcher(
    gitdir: Path,
    *,
    configured_base: str = "auto",
    observer: object | None = None,
    timer: ManualTimer | None = None,
) -> RefWatcher:
    fake = observer if observer is not None else RoutingFakeObserver()
    return RefWatcher(
        gitdir,
        configured_base,
        "origin",
        DEBOUNCE_MS,
        RECONCILE_S,
        observer_factories=(lambda: fake,),
        sleep=(timer or ManualTimer()).sleep,
    )


def test_diff_names_every_event_kind(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    watcher = _watcher(gitdir)
    before = watcher.snapshot()
    _write(gitdir / "refs" / "heads" / "main", B + "\n")
    (gitdir / "refs" / "heads" / "feature" / "x").unlink()
    _write(gitdir / "HEAD", "ref: refs/heads/other\n")
    _write(gitdir / "refs" / "tags" / "v1", A + "\n")
    _write(gitdir / "refs" / "remotes" / "origin" / "main", A + "\n")
    _write(gitdir / "refs" / "remotes" / "origin" / "topic", A + "\n")
    assert watcher.diff(before, watcher.snapshot()) == (
        RefEvent(RefEventKind.HEAD_MOVED, "other", None),
        RefEvent(RefEventKind.BRANCH_MOVED, "main", B),
        RefEvent(RefEventKind.BRANCH_DELETED, "feature/x", None),
        RefEvent(RefEventKind.TAG_MOVED, "v1", A),
        RefEvent(RefEventKind.REMOTE_MOVED, "origin/topic", A),
        RefEvent(RefEventKind.BASE_TIP_MOVED, "origin/main", A),
    )


def test_a_local_base_tip_move_is_a_branch_move_and_a_base_tip_move(tmp_path: Path) -> None:
    """With no remote-tracking ref the base tip is the local branch (spec §6.5):
    a commit on it both refreshes it and re-checks the merge-bases."""
    gitdir = _gitdir(tmp_path)
    (gitdir / "packed-refs").unlink()  # no remote
    watcher = _watcher(gitdir)
    before = watcher.snapshot()
    _write(gitdir / "refs" / "heads" / "main", B + "\n")
    assert watcher.diff(before, watcher.snapshot()) == (
        RefEvent(RefEventKind.BRANCH_MOVED, "main", B),
        RefEvent(RefEventKind.BASE_TIP_MOVED, "main", B),
    )


def test_the_base_tip_is_chosen_again_on_every_snapshot(tmp_path: Path) -> None:
    """#317: a remote the first push or fetch adds after the watch started takes
    over as the base tip (spec §6.5's rule, the plumbing twin of
    ``resolve_base_branch``); a local base move is then a branch move only."""
    gitdir = _gitdir(tmp_path)
    (gitdir / "packed-refs").unlink()
    watcher = _watcher(gitdir)
    no_remote = watcher.snapshot()
    _write(gitdir / "refs" / "remotes" / "origin" / "main", A + "\n")  # push -u: the same tip
    pushed = watcher.snapshot()
    assert watcher.diff(no_remote, pushed) == ()
    _write(gitdir / "refs" / "remotes" / "origin" / "main", C + "\n")
    fetched = watcher.snapshot()
    assert watcher.diff(pushed, fetched) == (
        RefEvent(RefEventKind.BASE_TIP_MOVED, "origin/main", C),
    )
    _write(gitdir / "refs" / "heads" / "main", B + "\n")
    assert watcher.diff(fetched, watcher.snapshot()) == (
        RefEvent(RefEventKind.BRANCH_MOVED, "main", B),
    )


def test_the_first_commit_of_an_unborn_base_moves_the_base_tip(tmp_path: Path) -> None:
    """A repository with no commit when the watch started has no base yet; its
    first commit gives it one, and the merge-bases are checked against it."""
    gitdir = _gitdir(tmp_path)
    (gitdir / "packed-refs").unlink()
    (gitdir / "refs" / "heads" / "main").unlink()
    (gitdir / "refs" / "heads" / "feature" / "x").unlink()
    watcher = _watcher(gitdir)
    unborn = watcher.snapshot()
    _write(gitdir / "refs" / "heads" / "main", A + "\n")
    assert watcher.diff(unborn, watcher.snapshot()) == (
        RefEvent(RefEventKind.BRANCH_MOVED, "main", A),
        RefEvent(RefEventKind.BASE_TIP_MOVED, "main", A),
    )


def test_an_explicit_base_is_the_only_candidate(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    _write(gitdir / "refs" / "heads" / "develop", A + "\n")
    watcher = _watcher(gitdir, configured_base="develop")
    before = watcher.snapshot()
    _write(gitdir / "refs" / "remotes" / "origin" / "main", A + "\n")  # not the base
    _write(gitdir / "refs" / "heads" / "develop", B + "\n")
    assert watcher.diff(before, watcher.snapshot()) == (
        RefEvent(RefEventKind.BRANCH_MOVED, "develop", B),
        RefEvent(RefEventKind.REMOTE_MOVED, "origin/main", A),
        RefEvent(RefEventKind.BASE_TIP_MOVED, "develop", B),
    )


def test_a_checkout_names_the_head_it_moved_to(tmp_path: Path) -> None:
    """``HEAD_MOVED`` carries the commit the new HEAD resolves to, so the runner
    can tell a pass that already indexed it (#317)."""
    gitdir = _gitdir(tmp_path)
    watcher = _watcher(gitdir)
    before = watcher.snapshot()
    _write(gitdir / "HEAD", "ref: refs/heads/feature/x\n")
    assert watcher.diff(before, watcher.snapshot()) == (
        RefEvent(RefEventKind.HEAD_MOVED, "feature/x", B),
    )
    detached = watcher.snapshot()
    _write(gitdir / "HEAD", C + "\n")
    assert watcher.diff(detached, watcher.snapshot()) == (
        RefEvent(RefEventKind.HEAD_MOVED, f"detached-{C[:7]}", C),
    )


def test_a_ref_that_moved_into_packed_refs_unchanged_is_no_event(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    watcher = _watcher(gitdir)
    before = watcher.snapshot()
    (gitdir / "refs" / "heads" / "main").unlink()
    _write(
        gitdir / "packed-refs", f"# pack-refs\n{C} refs/remotes/origin/main\n{A} refs/heads/main\n"
    )
    assert watcher.diff(before, watcher.snapshot()) == ()


@pytest.mark.parametrize(
    ("head", "name"),
    [
        ("ref: refs/heads/feature/x", "feature/x"),
        (A, f"detached-{A[:7]}"),
        ("ref: refs/remotes/origin/main", "origin/main"),
        ("", NON_GIT_BRANCH_NAME),
    ],
)
def test_the_head_names_the_branch_the_working_tree_pass_stamps(head: str, name: str) -> None:
    assert head_branch_name(head) == name


def test_only_plumbing_paths_wake_the_watcher(tmp_path: Path) -> None:
    gitdir = _gitdir(tmp_path)
    watcher = _watcher(gitdir)
    wakes = [
        gitdir / "HEAD",
        gitdir / "packed-refs",
        gitdir / "logs" / "HEAD",
        gitdir / "refs" / "heads" / "feature" / "y",
        gitdir / "refs" / "remotes" / "origin" / "main",
    ]
    quiet = [
        gitdir / "refs" / "heads" / "main.lock",
        gitdir / "HEAD.lock",
        gitdir / "index",
        gitdir / "objects" / "ab" / "cdef",
        gitdir / "COMMIT_EDITMSG",
    ]
    assert [watcher.is_ref_path(p) for p in wakes] == [True] * len(wakes)
    assert [watcher.is_ref_path(p) for p in quiet] == [False] * len(quiet)


async def _started(watcher: RefWatcher, timer: ManualTimer, received: list) -> asyncio.Task:
    """Start the watch; ``received`` then holds the diffs only (the start-up
    report is checked in its own test)."""

    async def on_events(events: tuple[RefEvent, ...]) -> None:
        received.append(events)

    task = asyncio.create_task(watcher.run_until_cancelled(on_events))
    await timer.wait_for_sleeps(1)  # the baseline is taken; parked on the tick
    assert [e.kind for batch in received for e in batch] == [RefEventKind.HEAD_AT_START]
    received.clear()
    return task


async def test_the_first_snapshot_reports_its_head_once(tmp_path: Path) -> None:
    """#317: a checkout or commit made while the startup pass ran is already in
    the first snapshot, so no diff will ever report it. The watch reports that
    snapshot's HEAD once, and the runner compares it with the served row."""
    gitdir, timer = _gitdir(tmp_path), ManualTimer()
    received: list = []

    async def on_events(events: tuple[RefEvent, ...]) -> None:
        received.append(events)

    task = asyncio.create_task(_watcher(gitdir, timer=timer).run_until_cancelled(on_events))
    await timer.wait_for_sleeps(1)
    timer.advance(RECONCILE_S)  # an idle tick reports nothing more
    await timer.wait_for_sleeps(2)
    await _stopped(task)
    assert received == [(RefEvent(RefEventKind.HEAD_AT_START, "main", A),)]


async def _stopped(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_the_watch_covers_the_plumbing_paths_and_never_the_objects(tmp_path: Path) -> None:
    gitdir, observer, timer = _gitdir(tmp_path), RoutingFakeObserver(), ManualTimer()
    task = await _started(_watcher(gitdir, observer=observer, timer=timer), timer, [])
    scheduled = {(Path(path), recursive) for _handler, path, recursive in observer._handlers}
    await _stopped(task)
    assert scheduled == {(gitdir, False), (gitdir / "logs", False), (gitdir / "refs", True)}
    assert observer.started is False  # stopped with the task


async def test_two_events_in_the_debounce_window_coalesce_into_one_delivery(
    tmp_path: Path,
) -> None:
    """Quiet-period debounce (spec §6.8c): the window restarts on every event, so
    a burst longer than the window still yields one diff, taken when it ends."""
    gitdir, observer, timer = _gitdir(tmp_path), RoutingFakeObserver(), ManualTimer()
    received: list = []
    task = await _started(_watcher(gitdir, observer=observer, timer=timer), timer, received)
    _write(gitdir / "refs" / "heads" / "main", B + "\n")
    observer.fire(str(gitdir / "refs" / "heads" / "main"))
    await timer.wait_for_sleeps(2)  # woken; the debounce runs
    timer.advance(0.5)
    _write(gitdir / "refs" / "heads" / "main", C + "\n")
    observer.fire(str(gitdir / "logs" / "HEAD"))
    await timer.wait_for_sleeps(3)  # the second event restarted the quiet period
    timer.advance(0.5)  # a full window after the first event, half after the second
    assert received == [] and timer.sleeps_started == 3
    timer.advance(0.5)
    await timer.wait_for_sleeps(4)  # delivered, parked on the next tick
    await _stopped(task)
    assert received == [(RefEvent(RefEventKind.BRANCH_MOVED, "main", C),)]
    assert timer.durations == [RECONCILE_S, 1.0, 1.0, RECONCILE_S]


async def test_events_are_a_wake_up_not_the_truth(tmp_path: Path) -> None:
    """A lock file never wakes the watcher; a wake-up whose snapshot is unchanged
    delivers nothing."""
    gitdir, observer, timer = _gitdir(tmp_path), RoutingFakeObserver(), ManualTimer()
    received: list = []
    task = await _started(_watcher(gitdir, observer=observer, timer=timer), timer, received)
    observer.fire(str(gitdir / "refs" / "heads" / "main.lock"))
    observer.fire_moved(str(gitdir / "HEAD.lock"), str(gitdir / "HEAD"))
    await timer.wait_for_sleeps(2)
    timer.advance(1.0)
    await timer.wait_for_sleeps(3)
    await _stopped(task)
    assert received == []


async def test_the_reconciliation_tick_re_snapshots_without_an_event(tmp_path: Path) -> None:
    """The inotify-overflow safety net (spec §6.8): a move no event reported is
    still delivered on the next tick."""
    gitdir, timer = _gitdir(tmp_path), ManualTimer()
    received: list = []
    task = await _started(_watcher(gitdir, timer=timer), timer, received)
    _write(gitdir / "refs" / "heads" / "feature" / "x", A + "\n")
    timer.advance(RECONCILE_S)
    await timer.wait_for_sleeps(2)
    await _stopped(task)
    assert received == [(RefEvent(RefEventKind.BRANCH_MOVED, "feature/x", A),)]


async def test_a_failing_delivery_never_stops_the_watcher(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    gitdir, timer = _gitdir(tmp_path), ManualTimer()
    delivered: list = []

    async def on_events(events: tuple[RefEvent, ...]) -> None:
        delivered.append(events)
        if len(delivered) == 2:  # the first diff, after the start-up report
            raise RuntimeError("queue closed")

    task = asyncio.create_task(_watcher(gitdir, timer=timer).run_until_cancelled(on_events))
    await timer.wait_for_sleeps(1)
    with caplog.at_level(logging.ERROR, logger="pydocs-mcp"):
        _write(gitdir / "refs" / "heads" / "main", B + "\n")
        timer.advance(RECONCILE_S)
        await timer.wait_for_sleeps(2)
    _write(gitdir / "refs" / "heads" / "main", C + "\n")
    timer.advance(RECONCILE_S)
    await timer.wait_for_sleeps(3)
    await _stopped(task)
    assert len(delivered) == 3
    assert "ref_watch_delivery_failed" in caplog.text


class _StartFails(RoutingFakeObserver):
    def start(self) -> None:
        raise OSError("inotify watch limit reached")


class _SecondScheduleFails(RoutingFakeObserver):
    """watchdog starts nothing on ``schedule`` before ``start``, but a failure
    part-way leaves the observer holding the watches that did succeed; the
    real ``start`` also keeps the emitters it started when a later one fails."""

    def __init__(self) -> None:
        super().__init__()
        self.stop_calls = 0

    def schedule(self, handler: object, path: str, recursive: bool = False) -> object:
        if self._handlers:
            raise OSError("inotify instance limit reached")
        return super().schedule(handler, path, recursive)

    def stop(self) -> None:
        self.stop_calls += 1
        super().stop()


async def test_a_partly_started_observer_is_stopped_before_the_fallback(tmp_path: Path) -> None:
    """#317: the fallback exists for the inotify limits; the failed observer's
    live watches must not outlive the attempt (an unconsumed event queue)."""
    gitdir, failed, fallback = _gitdir(tmp_path), _SecondScheduleFails(), RoutingFakeObserver()
    timer = ManualTimer()
    watcher = RefWatcher(
        gitdir,
        "auto",
        "origin",
        DEBOUNCE_MS,
        RECONCILE_S,
        observer_factories=(lambda: failed, lambda: fallback),
        sleep=timer.sleep,
    )
    task = await _started(watcher, timer, [])
    assert (failed.stop_calls, fallback.started) == (1, True)
    await _stopped(task)


async def test_the_polling_observer_takes_over_when_inotify_cannot_start(tmp_path: Path) -> None:
    gitdir, fallback, timer = _gitdir(tmp_path), RoutingFakeObserver(), ManualTimer()
    watcher = RefWatcher(
        gitdir,
        "auto",
        "origin",
        DEBOUNCE_MS,
        RECONCILE_S,
        observer_factories=(_StartFails, lambda: fallback),
        sleep=timer.sleep,
    )
    task = await _started(watcher, timer, [])
    assert fallback.started is True
    await _stopped(task)


async def test_no_observer_at_all_logs_and_serves_without_refresh(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    watcher = RefWatcher(
        _gitdir(tmp_path),
        "auto",
        "origin",
        DEBOUNCE_MS,
        RECONCILE_S,
        observer_factories=(_StartFails, _StartFails),
        sleep=ManualTimer().sleep,
    )

    async def on_events(events: tuple[RefEvent, ...]) -> None:
        raise AssertionError("no watch, no events")

    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await watcher.run_until_cancelled(on_events)  # returns: nothing to watch
    events = [json.loads(r.getMessage()) for r in caplog.records]
    assert [e["event"] for e in events] == ["ref_watch_unavailable"]
    assert "inotify watch limit reached" in events[0]["error"]
