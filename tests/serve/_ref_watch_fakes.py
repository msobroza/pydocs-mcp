"""Fakes for driving the ref watcher deterministically (#317).

``ManualTimer`` is a ``sleep`` whose clock moves only when the test says so. The
ref watcher takes its ``sleep`` as a field (the debounce and the reconciliation
tick are both sleeps), so a test drives both without waiting: ``advance``
resolves every sleep whose deadline has passed, and ``wait_for_sleeps(n)``
blocks until the watcher has started its ``n``-th sleep — the point where it is
parked again and everything before it has happened.

``RoutingFakeObserver`` delivers an event only to the watches that cover its
path, as watchdog does. The base ``FakeObserver`` hands every event to every
handler, which is harmless for the file watcher's single root watch but would
multiply the ref watcher's wake-ups by its number of watches.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests._fakes import FakeObserver

# A safety net for a broken watcher, never a synchronization device: every
# wait below is released by the watcher's own progress.
_STUCK_AFTER_SECONDS = 5.0


class ManualTimer:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps_started = 0
        self.durations: list[float] = []
        self._pending: list[tuple[float, asyncio.Future[None]]] = []
        self._changed = asyncio.Condition()

    async def sleep(self, seconds: float) -> None:
        entry = (self.now + seconds, asyncio.get_running_loop().create_future())
        self._pending.append(entry)
        async with self._changed:
            self.sleeps_started += 1
            self.durations.append(seconds)
            self._changed.notify_all()
        try:
            await entry[1]
        finally:
            self._pending.remove(entry)

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for deadline, future in list(self._pending):
            if deadline <= self.now and not future.done():
                future.set_result(None)

    async def wait_for_sleeps(self, count: int) -> None:
        async with self._changed:
            await asyncio.wait_for(
                self._changed.wait_for(lambda: self.sleeps_started >= count),
                _STUCK_AFTER_SECONDS,
            )


def _covers(root: Path, recursive: bool, path: Path) -> bool:
    return path.parent == root or (recursive and root in path.parents)


class RoutingFakeObserver(FakeObserver):
    def _dispatch(self, event) -> None:  # type: ignore[no-untyped-def]
        paths = [Path(event.src_path), *([Path(event.dest_path)] if event.dest_path else [])]
        for handler, root, recursive in self._handlers:
            if any(_covers(Path(root), recursive, path) for path in paths):
                handler.dispatch(event)  # type: ignore[attr-defined]
