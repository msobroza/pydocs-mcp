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

        Returns False for: directory events (no extension match),
        non-watched extensions, paths matching any `ignore_globs`
        pattern. Used by the watchdog event handler before queueing.
        """
        if path.suffix not in self.extensions:
            return False
        path_str = str(path)
        for pattern in self.ignore_globs:
            if fnmatch.fnmatch(path_str, pattern):
                return False
        return True

    async def run_until_cancelled(
        self, on_change: Callable[[], Awaitable[None]],
    ) -> None:
        """Start the observer, consume events, fire `on_change` on debounce.

        Cancelling the parent task (via `asyncio.Task.cancel()` or a
        propagated `KeyboardInterrupt`) stops the observer and unwinds
        cleanly. See spec Decisions E + G.

        Stub for now — wired in later tasks.
        """
        raise NotImplementedError("filled in by later TDD task")
