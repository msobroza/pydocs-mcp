"""File-system watcher for `pydocs-mcp serve --watch` (spec §4.1).

The MCP server runs on the main thread (Phase 2 of `_cmd_serve`); this
module runs alongside it as an asyncio task that consumes filesystem
events from `watchdog.Observer`'s native thread and re-triggers
indexing on debounce.

Lazy import boundary: `watchdog` lives behind the `[watch]` extras
group; importing it at module top would crash `pydocs-mcp serve`
(no `--watch`) for users who haven't installed the extras. The
constructor below resolves the import once at first use — if the
extras aren't present, raises `ServiceUnavailableError` with the
install hint instead of letting an `ImportError` bubble up cryptically.

Event-loop bridge: `watchdog.Observer` runs in its own native thread.
We give the event handler a reference to the asyncio loop + queue and
let it call `loop.call_soon_threadsafe(queue.put_nowait, path)` so
the consumer side sees the event on the right thread.
"""
from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError

log = logging.getLogger("pydocs-mcp.watch")

_INSTALL_HINT = (
    "--watch requires the 'watch' extras. Install via:\n"
    "    pip install pydocs-mcp[watch]"
)


def _load_watchdog():
    """Resolve `watchdog.observers.Observer`.

    Isolated so tests can monkeypatch `builtins.__import__` to simulate
    the no-extras case without touching the actual site-packages tree.

    The matching `FileSystemEventHandler` class is NOT imported here —
    `_Handler` (inside `run_until_cancelled`) uses duck typing
    (watchdog's `Observer.schedule(handler, ...)` only calls
    `handler.on_any_event(event)`; no isinstance check). Decoupling
    keeps the test path watchdog-free when callers pass
    `observer_factory=FakeObserver`.
    """
    try:
        from watchdog.observers import Observer
    except ImportError as exc:
        raise ServiceUnavailableError(_INSTALL_HINT) from exc
    return Observer


@dataclass(frozen=True, slots=True)
class FileWatcher:
    """File-system watcher value object (spec §4.1 deliverable 3).

    Frozen + slots: state (queue, lock, pending flag) lives on
    asyncio-owned objects threaded through `run_until_cancelled` rather
    than as mutable dataclass fields — keeps the constructor cheap and
    `dataclasses.replace`-compatible for future variant tuning.
    """

    root: Path
    extensions: tuple[str, ...]
    ignore_globs: tuple[str, ...]
    debounce_ms: int
    # Allows tests to inject a `FakeObserver` without touching watchdog.
    # Production callers leave it None → constructor resolves the real
    # `watchdog.observers.Observer` lazily.
    observer_factory: Callable[[], object] | None = field(default=None)

    def __post_init__(self) -> None:
        # WHY: resolve the watchdog import (or raise the install hint) at
        # construction time rather than at first event — startup failure
        # is easier to diagnose than mid-run "why isn't my watcher firing".
        if self.observer_factory is None:
            object.__setattr__(self, "observer_factory", _load_watchdog())

    def _matches(self, path: Path) -> bool:
        """Pure-function event filter — returns True iff the path is
        a candidate for triggering a reindex.

        Extensions are compared case-insensitively (path.suffix.lower())
        so editors that save as `Setup.PY` on case-insensitive filesystems
        (macOS APFS / Windows NTFS by default) still trigger reindex.
        Defaults in WatchConfig are lowercase by convention.

        Returns False for: non-watched extensions, paths matching any
        `ignore_globs` pattern.
        """
        if path.suffix.lower() not in self.extensions:
            return False
        path_str = str(path)
        return not any(
            fnmatch.fnmatch(path_str, pattern) for pattern in self.ignore_globs
        )

    async def run_until_cancelled(
        self, on_change: Callable[[], Awaitable[None]],
    ) -> None:
        """Start the observer, consume filtered events, fire `on_change`.

        Cancelling the parent task stops the observer and unwinds cleanly.
        See spec Decisions E + G.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Path] = asyncio.Queue()

        watcher_self = self

        # Plain class (no FileSystemEventHandler parent): watchdog's
        # Observer.schedule() is duck-typed — it only calls
        # `handler.on_any_event(event)`. Dropping the inheritance keeps the
        # test path watchdog-free when callers pass `observer_factory=
        # FakeObserver`, so unit tests don't depend on the `[watch]` extras.
        class _Handler:
            def on_any_event(self, event) -> None:
                # WHY: `watchdog` calls this from its own native thread.
                # `loop.call_soon_threadsafe(queue.put_nowait, ...)` is the
                # documented bridge — never `queue.put_nowait` directly,
                # which would race the asyncio side.
                path = Path(event.src_path)
                if not watcher_self._matches(path):
                    return
                # Loop closed (observer being torn down): drop the event.
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(queue.put_nowait, path)

        observer = self.observer_factory()  # type: ignore[misc]
        observer.schedule(_Handler(), str(self.root), recursive=True)
        observer.start()
        try:
            await self._consume(queue, on_change)
        finally:
            observer.stop()
            observer.join(timeout=2.0)

    async def _consume(
        self,
        queue: asyncio.Queue,
        on_change: Callable[[], Awaitable[None]],
    ) -> None:
        """Consume queued events; debounce + coalesce per spec Decision E.

        Concurrency model:
        - Only one `on_change()` runs at a time (`reindex_lock`).
        - Events arriving while the lock is held set `pending["flag"]=True`
          so a single follow-up reindex fires after the current one releases.
        - Burst events during an in-flight reindex coalesce to ONE
          follow-up regardless of count (AC-5).
        """
        debounce_s = self.debounce_ms / 1000.0
        reindex_lock = asyncio.Lock()
        # Mutable closure state — `_consume` is the single async consumer,
        # so no cross-task aliasing concerns. The `_trigger_with_followup`
        # coroutine is scheduled via `asyncio.create_task` from this same
        # consumer, so writes are serialized through the event loop.
        #
        # `deferred_paths` accumulates the paths from every trigger that
        # arrived while a reindex was in flight — the follow-up reindex
        # then carries the full path list into `_log_trigger`, so the
        # operator's log line names the files that motivated the
        # follow-up (not an empty list).
        deferred_paths: list[Path] = []
        # Strong refs to spawned trigger tasks — without holding these,
        # the event loop may garbage-collect a pending task and emit
        # "Task was destroyed but it is pending" warnings. The set
        # discards each task when it completes via `add_done_callback`.
        bg_tasks: set[asyncio.Task] = set()

        async def _drain_and_fire(paths: list[Path]) -> None:
            self._log_trigger(paths)
            await on_change()

        async def _trigger_with_followup(paths: list[Path]) -> None:
            # If a reindex is in flight, accumulate the paths so the
            # in-flight reindex's post-fire drain can carry them into the
            # follow-up log line.
            if reindex_lock.locked():
                deferred_paths.extend(paths)
                log.debug("watch: in-flight reindex; queued follow-up")
                return
            async with reindex_lock:
                await _drain_and_fire(paths)
                # `while` (not `if`): a continuously-edited workspace can
                # queue more events DURING the follow-up reindex itself;
                # keep draining until idle so we don't silently miss a
                # burst that lands while we're still inside the lock.
                while deferred_paths:
                    follow_up = deferred_paths.copy()
                    deferred_paths.clear()
                    log.info("watch: in-flight follow-up reindex firing")
                    await _drain_and_fire(follow_up)

        while True:
            first_path = await queue.get()
            pending_paths: list[Path] = [first_path]
            while True:
                try:
                    nxt = await asyncio.wait_for(queue.get(), timeout=debounce_s)
                    pending_paths.append(nxt)
                except TimeoutError:
                    break
            # Fire-and-forget so the consumer loop can immediately resume
            # draining queue events into `pending["flag"]` while the
            # current reindex runs. Hold a strong ref in `bg_tasks` to
            # prevent the event loop from GC-ing the pending task.
            task = asyncio.create_task(
                _trigger_with_followup(pending_paths),
                name="watcher-trigger",
            )
            bg_tasks.add(task)
            task.add_done_callback(bg_tasks.discard)

    def _log_trigger(self, paths: list[Path]) -> None:
        """Log the trigger paths (cap at 3 + a count to keep logs sane).

        Spec Open Item O5 — INFO line per trigger with up to 3 changed
        paths. Larger bursts (editor save-all, git checkout) collapse
        into `(+N more)` suffix so the log stays readable.
        """
        if not paths:
            return
        head = ", ".join(str(p) for p in paths[:3])
        if len(paths) > 3:
            log.info("watch: reindex triggered (%s, +%d more)", head, len(paths) - 3)
        else:
            log.info("watch: reindex triggered (%s)", head)
