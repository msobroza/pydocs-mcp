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
    """Resolve `watchdog.observers.Observer` + `watchdog.events.FileSystemEventHandler`.

    Isolated so tests can monkeypatch `builtins.__import__` to simulate
    the no-extras case without touching the actual site-packages tree.
    """
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError as exc:
        raise ServiceUnavailableError(_INSTALL_HINT) from exc
    return Observer, FileSystemEventHandler


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
            Observer, _ = _load_watchdog()
            object.__setattr__(self, "observer_factory", Observer)

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
        for pattern in self.ignore_globs:
            if fnmatch.fnmatch(path_str, pattern):
                return False
        return True

    async def run_until_cancelled(
        self, on_change: Callable[[], Awaitable[None]],
    ) -> None:
        """Start the observer, consume filtered events, fire `on_change`.

        Cancelling the parent task stops the observer and unwinds cleanly.
        See spec Decisions E + G.
        """
        _, FileSystemEventHandler = _load_watchdog()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Path] = asyncio.Queue()

        watcher_self = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event) -> None:  # noqa: ANN001
                # WHY: `watchdog` calls this from its own native thread.
                # `loop.call_soon_threadsafe(queue.put_nowait, ...)` is the
                # documented bridge — never `queue.put_nowait` directly,
                # which would race the asyncio side.
                path = Path(event.src_path)
                if not watcher_self._matches(path):
                    return
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, path)
                except RuntimeError:
                    # Loop closed — observer is being torn down. Drop event.
                    pass

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
        """Consume queued events, debounce, fire `on_change` per spec Decision E.

        Debounce algorithm: pop the first event, then repeatedly wait
        `debounce_ms` more — if another event arrives during the wait,
        reset the timer (the wait coalesces it). Once the timer expires
        without a new event, fire `on_change`.
        """
        debounce_s = self.debounce_ms / 1000.0
        while True:
            # Block until something arrives — no work to do otherwise.
            first_path = await queue.get()
            pending_paths: list[Path] = [first_path]

            # Debounce loop — extend the window every time a new event
            # lands during the wait. Exits when wait_for times out
            # without seeing an event.
            while True:
                try:
                    nxt = await asyncio.wait_for(queue.get(), timeout=debounce_s)
                    pending_paths.append(nxt)
                except asyncio.TimeoutError:
                    break

            self._log_trigger(pending_paths)
            await on_change()

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
