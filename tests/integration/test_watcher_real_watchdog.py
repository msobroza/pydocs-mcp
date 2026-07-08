"""Real-watchdog integration: events must flow end-to-end from the native
observer thread into ``on_change``.

This is the test that fake-based unit tests can never provide: it exercises
watchdog's actual dispatch protocol (``handler.dispatch(event)``) and the
platform emitter's event shapes for both plain writes and atomic-save
renames. The dispatch-contract regression (handler implementing only
``on_any_event``) made ``serve --watch`` a silent no-op with the real
``[watch]`` extra while every FakeObserver unit test stayed green.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

pytest.importorskip("watchdog")

from pydocs_mcp.serve.watcher import FileWatcher


async def _wait_until(predicate, timeout_s: float = 8.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.parametrize("save_style", ["plain_write", "atomic_rename"])
async def test_real_observer_delivers_events_to_on_change(tmp_path: Path, save_style: str) -> None:
    (tmp_path / "app.py").write_text("x = 1\n")
    fires: list[int] = []
    watcher = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=50,
    )

    async def _on_change() -> None:
        fires.append(1)

    task = asyncio.create_task(watcher.run_until_cancelled(_on_change))
    # Native emitters (FSEvents / inotify) need a moment to arm before the
    # first mutation, or the event is silently missed.
    await asyncio.sleep(0.7)

    if save_style == "plain_write":
        (tmp_path / "app.py").write_text("x = 2\n")
    else:
        tmp = tmp_path / "app.py.tmp"
        tmp.write_text("x = 2\n")
        tmp.replace(tmp_path / "app.py")

    fired = await _wait_until(lambda: len(fires) > 0)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert fired, f"{save_style} never reached on_change through the real observer"
