"""Ref-driven refresh (spec §6.8, #317): watch git's plumbing paths, re-read the
ref snapshot on every wake-up and on a slow reconciliation tick, diff it against
the previous one, and hand the events to the job queue.

Events are a wake-up, not the truth: a ``.lock`` rename, a ref moved into
``packed-refs`` unchanged, or a rebase that rewrites a branch a hundred times all
produce exactly the events their final state warrants. The snapshot reads the
plumbing files only (``git/refs.py``) — detecting a change never spawns git, and
neither does choosing the base tip, which is re-chosen on every snapshot.

The first snapshot is reported once, as ``HEAD_AT_START``: a checkout or commit
made while the startup pass ran is already in it, so no diff would ever report it.

Same shape as ``serve/watcher.py``'s ``FileWatcher``: a frozen dataclass with
injectable observers, and ``sleep`` injectable too, so a test drives the debounce
and the tick with a manual clock instead of waiting.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydocs_mcp.application.branch_manifest import branch_display_name
from pydocs_mcp.application.branch_policy import snapshot_base_tip_ref
from pydocs_mcp.git.refs import (
    HEADS_PREFIX,
    REMOTES_PREFIX,
    SYMREF_PREFIX,
    list_refs,
    read_head,
    refs_home,
)

log = logging.getLogger("pydocs-mcp")

TAGS_PREFIX = "refs/tags/"
# What ``git symbolic-ref --short`` strips, in its order: the working-tree pass
# names a symbolic HEAD that way, and the two names must agree (#317).
_SHORT_REF_PREFIXES = (HEADS_PREFIX, TAGS_PREFIX, REMOTES_PREFIX, "refs/")
# Files directly under the gitdir (or the refs home) whose rewrite can move the
# snapshot; ``logs/HEAD`` is appended by every commit, checkout, reset or rebase.
_PLUMBING_FILE_NAMES = frozenset({"HEAD", "packed-refs"})
_LOCK_SUFFIX = ".lock"
_OBSERVER_JOIN_SECONDS = 2.0

RefEvents = tuple["RefEvent", ...]


class RefEventKind(StrEnum):
    """What moved between two snapshots (spec §6.8's event table)."""

    HEAD_MOVED = "head_moved"
    BRANCH_MOVED = "branch_moved"
    BRANCH_DELETED = "branch_deleted"
    BASE_TIP_MOVED = "base_tip_moved"
    TAG_MOVED = "tag_moved"
    REMOTE_MOVED = "remote_moved"
    # Not a move: the HEAD of the watch's first snapshot, reported once so the
    # served row is checked against it. A checkout or commit made while the
    # startup pass ran is already in that snapshot, so no diff reports it (#317).
    HEAD_AT_START = "head_at_start"


@dataclass(frozen=True, slots=True)
class RefEvent:
    """One move: the short ref name and its new sha (``None`` once gone). For
    the two HEAD events, the branch name the working-tree pass will stamp and
    the commit HEAD resolves to (``None`` while that branch is unborn)."""

    kind: RefEventKind
    name: str
    sha: str | None


@dataclass(frozen=True, slots=True)
class RefSnapshot:
    """The raw ``HEAD`` line and every local branch, tag and remote-tracking ref
    of the watched remote, full ref name → sha."""

    head: str
    heads: Mapping[str, str]
    tags: Mapping[str, str]
    remotes: Mapping[str, str]


def _short_ref_name(ref: str) -> str:
    for prefix in _SHORT_REF_PREFIXES:
        if ref.startswith(prefix):
            return ref.removeprefix(prefix)
    return ref


def head_branch_name(head: str) -> str:
    """The branch name the working-tree pass stamps for a raw ``HEAD`` line.

    The plumbing twin of ``branch_display_name(git.current_branch(),
    git.head_sha())`` (spec §2): the symbolic target's short name, including an
    unborn branch; ``detached-<sha7>`` for a raw sha; the non-git sentinel for an
    unreadable ``HEAD``. The queue keys jobs on it, so a file save and a checkout
    of the same branch coalesce, and the runner tells the working tree by it.
    """
    if head.startswith(SYMREF_PREFIX):
        return _short_ref_name(head.removeprefix(SYMREF_PREFIX).strip())
    return branch_display_name(None, head or None)


def _head_sha(snapshot: RefSnapshot) -> str | None:
    """The commit ``HEAD`` resolves to within the snapshot: the raw sha when
    detached, the local branch's tip when symbolic; ``None`` for an unborn
    branch, a symbolic ``HEAD`` outside ``refs/heads/`` or an unreadable one
    (a job carrying ``None`` always runs its pass)."""
    if not snapshot.head.startswith(SYMREF_PREFIX):
        return snapshot.head or None
    return snapshot.heads.get(snapshot.head.removeprefix(SYMREF_PREFIX).strip())


def _moved(previous: Mapping[str, str], current: Mapping[str, str]) -> Iterator[tuple[str, str]]:
    return ((ref, sha) for ref, sha in sorted(current.items()) if previous.get(ref) != sha)


def _gone(previous: Mapping[str, str], current: Mapping[str, str]) -> list[str]:
    return sorted(previous.keys() - current.keys())


def _ref_events(
    previous: Mapping[str, str],
    current: Mapping[str, str],
    *,
    moved: RefEventKind,
    gone: RefEventKind,
) -> RefEvents:
    """Refs created or moved, then refs removed, each group by name."""
    changed = (RefEvent(moved, _short_ref_name(ref), sha) for ref, sha in _moved(previous, current))
    removed = (RefEvent(gone, _short_ref_name(ref), None) for ref in _gone(previous, current))
    return (*changed, *removed)


@dataclass(frozen=True, slots=True)
class _BaseTip:
    """The base tip in one snapshot: its full ref and sha; empty when there is
    no base yet (an unborn repository, or no candidate branch)."""

    ref: str = ""
    sha: str | None = None


_NO_BASE_TIP = _BaseTip()


def _stop_observer(observer: object) -> None:
    observer.stop()  # type: ignore[attr-defined]
    # A thread never started cannot be joined: watchdog's ``start`` may fail
    # after starting some of its emitters, before the observer's own thread.
    with contextlib.suppress(RuntimeError):
        observer.join(timeout=_OBSERVER_JOIN_SECONDS)  # type: ignore[attr-defined]


def _refs_home_of(gitdir: Path) -> Path:
    """The common dir that holds the refs; the gitdir itself when an unreadable
    ``commondir`` hides it (the watch then covers what it can, never raises)."""
    try:
        return refs_home(gitdir).resolve()
    except (OSError, ValueError):
        return gitdir


def _load_ref_observers() -> tuple[Callable[[], object], ...]:
    """inotify (or the platform's native observer), then polling (spec §6.8).

    Deferred import, like ``serve/watcher.py``'s ``_load_watchdog``: a test that
    injects observers never loads watchdog.
    """
    from watchdog.observers import Observer
    from watchdog.observers.polling import PollingObserver

    return (Observer, PollingObserver)


@dataclass(frozen=True, slots=True)
class RefWatcher:
    """Watches one repository's refs and reports what moved (spec §6.8)."""

    gitdir: Path
    # ``git.branches.base`` (``auto`` or a branch name): the base tip is chosen
    # from it on every snapshot, never frozen at start (#317).
    configured_base: str
    remote: str
    debounce_ms: int
    reconcile_seconds: int
    # Tried in order until one starts; empty = watchdog's native observer, then
    # its polling observer.
    observer_factories: tuple[Callable[[], object], ...] = ()
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _refs_home: Path = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Resolved like FileWatcher's root: macOS FSEvents reports events under
        # the realpath, and the wake-up filter compares paths.
        object.__setattr__(self, "gitdir", self.gitdir.resolve())
        object.__setattr__(self, "_refs_home", _refs_home_of(self.gitdir))
        if not self.observer_factories:
            object.__setattr__(self, "observer_factories", _load_ref_observers())

    # ── The snapshot and its diff ─────────────────────────────────────────

    def snapshot(self) -> RefSnapshot:
        """Blocking plumbing reads (no subprocess); call it off the event loop."""
        return RefSnapshot(
            head=read_head(self.gitdir),
            heads=list_refs(self.gitdir, HEADS_PREFIX),
            tags=list_refs(self.gitdir, TAGS_PREFIX),
            remotes=list_refs(self.gitdir, f"{REMOTES_PREFIX}{self.remote}/"),
        )

    def diff(self, previous: RefSnapshot, current: RefSnapshot) -> RefEvents:
        return (
            *self._head_events(previous, current),
            *_ref_events(
                previous.heads,
                current.heads,
                moved=RefEventKind.BRANCH_MOVED,
                gone=RefEventKind.BRANCH_DELETED,
            ),
            # A deleted tag moves the retention window as much as a new one.
            *_ref_events(
                previous.tags,
                current.tags,
                moved=RefEventKind.TAG_MOVED,
                gone=RefEventKind.TAG_MOVED,
            ),
            *self._remote_events(previous, current),
            *self._base_tip_events(previous, current),
        )

    def start_report(self, first: RefSnapshot) -> RefEvents:
        """``HEAD_AT_START`` for the first snapshot's HEAD; nothing when it is
        unreadable (no branch to name)."""
        if not first.head:
            return ()
        name = head_branch_name(first.head)
        return (RefEvent(RefEventKind.HEAD_AT_START, name, _head_sha(first)),)

    def _head_events(self, previous: RefSnapshot, current: RefSnapshot) -> RefEvents:
        if current.head == previous.head:
            return ()
        name = head_branch_name(current.head)
        return (RefEvent(RefEventKind.HEAD_MOVED, name, _head_sha(current)),)

    def _remote_events(self, previous: RefSnapshot, current: RefSnapshot) -> RefEvents:
        """Remote-tracking moves other than the base tip's (the remote lane's)."""
        base_refs = {self._base_tip(previous).ref, self._base_tip(current).ref}
        changed = [ref for ref, _ in _moved(previous.remotes, current.remotes)]
        refs = sorted({*changed, *_gone(previous.remotes, current.remotes)} - base_refs)
        return tuple(
            RefEvent(RefEventKind.REMOTE_MOVED, _short_ref_name(ref), current.remotes.get(ref))
            for ref in refs
        )

    def _base_tip_events(self, previous: RefSnapshot, current: RefSnapshot) -> RefEvents:
        """Spec §6.5: a base-tip move, local or fetched, re-checks every tracked
        branch's merge-base. Compared by sha: the first push that creates the
        remote-tracking ref hands it the tip at the same commit, which moves no
        merge-base."""
        before, after = self._base_tip(previous), self._base_tip(current)
        if before.sha == after.sha:
            return ()
        name = _short_ref_name(after.ref or before.ref)
        return (RefEvent(RefEventKind.BASE_TIP_MOVED, name, after.sha),)

    def _base_tip(self, snapshot: RefSnapshot) -> _BaseTip:
        """Spec §6.5's tip — the remote-tracking ref when there is one, else the
        local branch — chosen in this snapshot, so a remote the first push adds
        or an unborn base's first commit is followed (#317)."""
        ref = snapshot_base_tip_ref(
            snapshot.heads,
            snapshot.remotes,
            configured_base=self.configured_base,
            remote=self.remote,
        )
        if ref is None:
            return _NO_BASE_TIP
        home = snapshot.remotes if ref.startswith(REMOTES_PREFIX) else snapshot.heads
        return _BaseTip(ref, home[ref])

    # ── The wake-up filter ────────────────────────────────────────────────

    def is_ref_path(self, path: Path) -> bool:
        """True for a path whose change can move the snapshot: ``HEAD``,
        ``packed-refs``, ``logs/HEAD`` or anything under ``refs/``; never a lock
        file (git's in-flight write), the index or the object store."""
        if path.name.endswith(_LOCK_SUFFIX):
            return False
        if path.name in _PLUMBING_FILE_NAMES and path.parent in (self.gitdir, self._refs_home):
            return True
        if path == self.gitdir / "logs" / "HEAD":
            return True
        refs_root = self._refs_home / "refs"
        return path == refs_root or refs_root in path.parents

    def _watch_paths(self) -> dict[Path, bool]:
        """Directory → recursive. A handful of watches on plumbing paths, never
        the object store or the working tree (spec §6.8's cost line)."""
        candidates = {
            self.gitdir: False,
            self.gitdir / "logs": False,
            self._refs_home: False,
            self._refs_home / "refs": True,
        }
        return {path: recursive for path, recursive in candidates.items() if path.is_dir()}

    # ── The loop ──────────────────────────────────────────────────────────

    async def run_until_cancelled(self, on_events: Callable[[RefEvents], Awaitable[None]]) -> None:
        """Watch until cancelled, calling ``on_events`` with each non-empty diff.

        Returns at once, after one ``ref_watch_unavailable`` log, when no
        observer can start: the server keeps serving without refresh (§6.11).
        """
        wakeups: asyncio.Queue[Path] = asyncio.Queue()
        observer = self._start_observer(_WakeUpBridge(self, asyncio.get_running_loop(), wakeups))
        if observer is None:
            return
        try:
            await self._consume(wakeups, on_events)
        finally:
            _stop_observer(observer)

    def _start_observer(self, handler: _WakeUpBridge) -> object | None:
        error: Exception | None = None
        for factory in self.observer_factories:
            observer, error = self._try_observer(factory, handler)
            if observer is not None:
                return observer
        log.warning(json.dumps({"event": "ref_watch_unavailable", "error": str(error)}))
        return None

    def _try_observer(
        self, factory: Callable[[], object], handler: _WakeUpBridge
    ) -> tuple[object | None, Exception | None]:
        try:
            observer = factory()
        except Exception as exc:
            return None, exc
        try:
            return self._started(observer, handler), None
        except Exception as exc:  # no inotify, a watch limit, an unreadable dir
            # watchdog's ``start`` starts its emitters one by one and, when one
            # fails, re-raises with the earlier ones still running: stop them,
            # or their inotify instances outlive the fallback (#317 review).
            _stop_observer(observer)
            return None, exc

    def _started(self, observer: object, handler: _WakeUpBridge) -> object:
        for path, recursive in self._watch_paths().items():
            observer.schedule(handler, str(path), recursive=recursive)  # type: ignore[attr-defined]
        observer.start()  # type: ignore[attr-defined]
        return observer

    async def _consume(
        self, wakeups: asyncio.Queue[Path], on_events: Callable[[RefEvents], Awaitable[None]]
    ) -> None:
        previous = await asyncio.to_thread(self.snapshot)
        # Read from the baseline itself: a move before it is in this report,
        # a move after it in a later diff — none falls between the two.
        if report := self.start_report(previous):
            await _deliver(on_events, report)
        while True:
            await self._wait_for_quiet_burst(wakeups)
            current = await asyncio.to_thread(self.snapshot)
            events = self.diff(previous, current)
            previous = current
            if events:
                await _deliver(on_events, events)

    async def _wait_for_quiet_burst(self, wakeups: asyncio.Queue[Path]) -> None:
        """Return on the reconciliation tick, or once a burst of wake-ups has
        been quiet for the debounce window (spec §6.8c: a burst longer than the
        window still yields one snapshot, taken when it goes quiet)."""
        if not await self._next_wake_up(wakeups, float(self.reconcile_seconds)):
            return
        while await self._next_wake_up(wakeups, self.debounce_ms / 1000.0):
            continue

    async def _next_wake_up(self, wakeups: asyncio.Queue[Path], timeout: float) -> bool:
        """True when a wake-up arrived before ``timeout`` elapsed on ``sleep``."""
        arrival = asyncio.ensure_future(wakeups.get())
        timer = asyncio.ensure_future(self.sleep(timeout))
        try:
            done, _ = await asyncio.wait((arrival, timer), return_when=asyncio.FIRST_COMPLETED)
        finally:
            # A cancelled ``get`` leaves its item queued: no wake-up is lost.
            for waiter in (arrival, timer):
                waiter.cancel()
            await asyncio.gather(arrival, timer, return_exceptions=True)
        return arrival in done


async def _deliver(on_events: Callable[[RefEvents], Awaitable[None]], events: RefEvents) -> None:
    try:
        await on_events(events)
    except Exception:
        # A failed hand-off must not end the watch: the next wake-up or tick
        # diffs against this snapshot, so only these events are lost, and the
        # per-response freshness probe still reports the branch stale.
        kinds = sorted({event.kind.value for event in events})
        log.exception(json.dumps({"event": "ref_watch_delivery_failed", "kinds": kinds}))


@dataclass(frozen=True, slots=True)
class _WakeUpBridge:
    """The watchdog handler: filters on watchdog's thread, wakes the loop.

    Duck-typed ``dispatch`` (no ``FileSystemEventHandler`` parent), the
    ``FileWatcher`` precedent: watchdog calls ``handler.dispatch(event)``.
    """

    watcher: RefWatcher
    loop: asyncio.AbstractEventLoop
    wakeups: asyncio.Queue[Path]

    def dispatch(self, event: object) -> None:
        # A move carries the final name in ``dest_path`` (a lock renamed over a ref).
        paths = [Path(str(getattr(event, "src_path", "")))]
        dest = getattr(event, "dest_path", "")
        if dest:
            paths.append(Path(str(dest)))
        if not any(self.watcher.is_ref_path(path) for path in paths):
            return
        # Loop closed (the watcher being torn down): drop the wake-up.
        with contextlib.suppress(RuntimeError):
            self.loop.call_soon_threadsafe(self.wakeups.put_nowait, paths[-1])


__all__ = (
    "TAGS_PREFIX",
    "RefEvent",
    "RefEventKind",
    "RefEvents",
    "RefSnapshot",
    "RefWatcher",
    "head_branch_name",
)
