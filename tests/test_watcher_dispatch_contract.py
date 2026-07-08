"""Watcher event-handler contract: real dispatch entrypoint + moved events.

Two regressions pinned here:

1. watchdog's ``BaseObserver.dispatch_events`` calls ``handler.dispatch(event)``
   — NOT ``on_any_event``. The watcher's handler used to implement only
   ``on_any_event``, so with the real ``[watch]`` extra installed every event
   raised AttributeError in the emitter thread and ``serve --watch`` never
   fired a reindex at all. ``FakeObserver`` now enforces the same contract.

2. Atomic-save editors (JetBrains safe-write, vim, gedit) write ``app.py.tmp``
   then rename it over ``app.py``, producing a moved event whose ``src_path``
   is the temp file and whose ``dest_path`` is the real one. The handler used
   to read only ``src_path`` — ``.tmp`` fails the extension filter, so those
   saves never triggered a reindex.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests._fakes import FakeObserver


async def _run_watcher(tmp_path: Path, fake: FakeObserver):
    """Start a FileWatcher over `fake`; return (task, fire_counter)."""
    from pydocs_mcp.serve.watcher import FileWatcher

    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=10,
        observer_factory=lambda: fake,
    )
    fires: list[int] = []

    async def _on_change() -> None:
        fires.append(1)

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    await asyncio.sleep(0.01)  # let the observer register the handler
    return task, fires


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_handler_satisfies_real_dispatch_contract(tmp_path: Path) -> None:
    """A plain modified event must reach on_change through `dispatch()` —
    the only entrypoint the real watchdog observer invokes."""
    fake = FakeObserver()
    task, fires = await _run_watcher(tmp_path, fake)
    fake.fire(str(tmp_path / "app.py"))
    await asyncio.sleep(0.05)
    await _stop(task)
    assert len(fires) == 1


async def test_atomic_save_rename_triggers_reindex(tmp_path: Path) -> None:
    """Moved event tmp → real: dest_path is the watched file; must fire."""
    fake = FakeObserver()
    task, fires = await _run_watcher(tmp_path, fake)
    fake.fire_moved(str(tmp_path / "app.py.tmp"), str(tmp_path / "app.py"))
    await asyncio.sleep(0.05)
    await _stop(task)
    assert len(fires) == 1


async def test_move_away_of_watched_file_triggers_reindex(tmp_path: Path) -> None:
    """Renaming a watched file OUT of watched space removes indexed content —
    src_path matches, so a reindex must fire."""
    fake = FakeObserver()
    task, fires = await _run_watcher(tmp_path, fake)
    fake.fire_moved(str(tmp_path / "app.py"), str(tmp_path / "app.py.bak"))
    await asyncio.sleep(0.05)
    await _stop(task)
    assert len(fires) == 1


async def test_unrelated_move_is_ignored(tmp_path: Path) -> None:
    fake = FakeObserver()
    task, fires = await _run_watcher(tmp_path, fake)
    fake.fire_moved(str(tmp_path / "a.tmp"), str(tmp_path / "b.log"))
    await asyncio.sleep(0.05)
    await _stop(task)
    assert fires == []


async def test_watched_to_watched_rename_coalesces_to_one_fire(tmp_path: Path) -> None:
    """A .py → .py rename matches on BOTH ends; debounce must coalesce the
    two queued paths into a single on_change."""
    fake = FakeObserver()
    task, fires = await _run_watcher(tmp_path, fake)
    fake.fire_moved(str(tmp_path / "old.py"), str(tmp_path / "new.py"))
    await asyncio.sleep(0.05)
    await _stop(task)
    assert len(fires) == 1
