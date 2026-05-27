"""Tests for the file watcher (`pydocs-mcp serve --watch`).

Mirrors spec §4.1 deliverable 6. The `FakeObserver` injected into
`FileWatcher` lets us drive events synchronously — no real `watchdog`
thread is involved, so tests stay fast and deterministic.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError


def test_watcher_module_importable() -> None:
    """The module itself imports without watchdog installed.

    `watchdog` import lives inside `FileWatcher.__post_init__` /
    constructor only, so users who never touch `--watch` pay zero cost.
    """
    from pydocs_mcp.serve import watcher  # noqa: F401


def test_watcher_construction_raises_when_watchdog_missing(monkeypatch) -> None:
    """AC-9: without the `[watch]` extras, constructor raises with the
    actionable install hint pointing at `pip install pydocs-mcp[watch]`."""
    import builtins

    real_import = builtins.__import__

    def _no_watchdog(name, *args, **kwargs):
        if name == "watchdog" or name.startswith("watchdog."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_watchdog)

    from pydocs_mcp.serve.watcher import FileWatcher

    with pytest.raises(ServiceUnavailableError) as exc_info:
        FileWatcher(
            root=Path("/tmp"),
            extensions=(".py",),
            ignore_globs=(),
            debounce_ms=500,
        )
    assert "pip install pydocs-mcp[watch]" in str(exc_info.value)


def test_watcher_construction_succeeds_when_watchdog_present(tmp_path: Path) -> None:
    """Real watchdog installed (project tests run under the dev extras)
    — constructor returns a FileWatcher instance."""
    pytest.importorskip("watchdog")
    from pydocs_mcp.serve.watcher import FileWatcher

    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=500,
    )
    assert fw.root == tmp_path
    assert fw.extensions == (".py",)
    assert fw.debounce_ms == 500


def test_fake_observer_injects_events_synchronously() -> None:
    """The FakeObserver test helper records start/stop/schedule calls
    and exposes a `.fire(path)` hook tests can call to inject events
    without filesystem timing nondeterminism."""
    from tests._fakes import FakeObserver

    obs = FakeObserver()
    assert not obs.started
    obs.start()
    assert obs.started

    fired: list[str] = []

    class _Handler:
        def on_any_event(self, event):
            fired.append(event.src_path)

    obs.schedule(_Handler(), "/tmp/some/dir", recursive=True)
    obs.fire("/tmp/some/dir/file.py")
    assert fired == ["/tmp/some/dir/file.py"]

    obs.stop()
    assert not obs.started
    obs.join()  # idempotent no-op


def test_fake_observer_fire_event_has_src_path_attr() -> None:
    """`fire(path)` synthesizes an event with the `src_path` attr the
    watchdog handler expects (mirrors `watchdog.events.FileSystemEvent`)."""
    from tests._fakes import FakeObserver

    obs = FakeObserver()
    obs.start()
    captured: list[object] = []

    class _Handler:
        def on_any_event(self, event):
            captured.append(event)

    obs.schedule(_Handler(), "/x", recursive=True)
    obs.fire("/x/a.py")
    assert hasattr(captured[0], "src_path")
    assert captured[0].src_path == "/x/a.py"


async def test_watcher_filters_unrelated_events(tmp_path: Path) -> None:
    """AC-3: `.pyc`, `__pycache__/`, `.git/` events do NOT trigger callback."""
    from tests._fakes import FakeObserver

    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py", ".md", ".ipynb"),
        ignore_globs=(
            "**/__pycache__/**",
            "**/.git/**",
            "**/*.pyc",
        ),
        debounce_ms=10,  # short for fast tests
        observer_factory=lambda: fake,
    )

    fire_count = 0

    async def _on_change() -> None:
        nonlocal fire_count
        fire_count += 1

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    # Give the watcher one tick to start the observer + register the handler.
    await asyncio.sleep(0.01)

    # These should ALL be filtered out:
    fake.fire(str(tmp_path / "x.pyc"))                       # bad extension
    fake.fire(str(tmp_path / "__pycache__" / "x.cpython.pyc"))  # ignore
    fake.fire(str(tmp_path / ".git" / "HEAD"))                  # ignore
    fake.fire(str(tmp_path / "x.png"))                          # bad ext

    # Wait past debounce; no callback should fire.
    await asyncio.sleep(0.05)
    assert fire_count == 0

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_watcher_fires_on_matching_extension(tmp_path: Path) -> None:
    """The positive case: a `.py` edit fires exactly one callback."""
    from tests._fakes import FakeObserver

    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=10,
        observer_factory=lambda: fake,
    )

    fire_count = 0

    async def _on_change() -> None:
        nonlocal fire_count
        fire_count += 1

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    await asyncio.sleep(0.01)

    fake.fire(str(tmp_path / "app.py"))
    await asyncio.sleep(0.05)
    assert fire_count == 1

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_watcher_matches_extension_case_insensitively(tmp_path: Path) -> None:
    """Regression test for the case-sensitivity bug flagged by code-quality
    and gstack reviewers on the FileWatcher skeleton.

    macOS APFS and Windows NTFS are case-insensitive by default but
    case-preserving — an editor saving as `Setup.PY` would silently miss
    reindex with case-sensitive `path.suffix in self.extensions`.
    Fix: `path.suffix.lower() not in self.extensions` (defaults are
    already lowercase by convention)."""
    from tests._fakes import FakeObserver

    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=10,
        observer_factory=lambda: fake,
    )

    fire_count = 0

    async def _on_change() -> None:
        nonlocal fire_count
        fire_count += 1

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    await asyncio.sleep(0.01)

    # Space the two fires past the 10ms debounce window so each gets
    # its own callback — the test is about the case-filter matching
    # both, not about debounce semantics (which has its own test).
    fake.fire(str(tmp_path / "Setup.PY"))  # uppercase extension
    await asyncio.sleep(0.05)
    fake.fire(str(tmp_path / "Module.Py"))  # mixed-case extension
    await asyncio.sleep(0.05)
    assert fire_count == 2  # both should match

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_watcher_debounces_burst_edits(tmp_path: Path) -> None:
    """AC-4: 3 events within `debounce_ms` produce exactly 1 callback."""
    from tests._fakes import FakeObserver

    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=50,
        observer_factory=lambda: fake,
    )

    fire_count = 0

    async def _on_change() -> None:
        nonlocal fire_count
        fire_count += 1

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    await asyncio.sleep(0.01)

    # Three rapid edits within the 50ms debounce window.
    fake.fire(str(tmp_path / "a.py"))
    fake.fire(str(tmp_path / "b.py"))
    fake.fire(str(tmp_path / "c.py"))

    # Wait > debounce_ms — exactly one callback should have fired.
    await asyncio.sleep(0.12)
    assert fire_count == 1, f"expected 1 callback, got {fire_count}"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_watcher_fires_after_debounce_window(tmp_path: Path) -> None:
    """AC-2: callback fires within debounce_ms + small headroom."""
    from tests._fakes import FakeObserver

    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=30,
        observer_factory=lambda: fake,
    )

    times: list[float] = []
    loop = asyncio.get_running_loop()

    async def _on_change() -> None:
        times.append(loop.time())

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    await asyncio.sleep(0.01)

    start = loop.time()
    fake.fire(str(tmp_path / "a.py"))
    # Should fire roughly debounce_ms after the last event.
    await asyncio.sleep(0.1)

    assert len(times) == 1
    elapsed = times[0] - start
    # 30ms debounce, allow 100ms slack for test-host scheduling jitter.
    assert 0.025 <= elapsed <= 0.130, (
        f"callback fired at {elapsed*1000:.1f}ms; expected ~30ms debounce"
    )

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_watcher_two_bursts_separated_by_idle_produce_two_callbacks(
    tmp_path: Path,
) -> None:
    """Sanity: two bursts separated by > debounce_ms idle → 2 callbacks."""
    from tests._fakes import FakeObserver

    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=30,
        observer_factory=lambda: fake,
    )

    fire_count = 0

    async def _on_change() -> None:
        nonlocal fire_count
        fire_count += 1

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    await asyncio.sleep(0.01)

    # First burst.
    fake.fire(str(tmp_path / "a.py"))
    fake.fire(str(tmp_path / "b.py"))
    await asyncio.sleep(0.1)  # well past debounce, callback ran

    # Idle, then second burst.
    fake.fire(str(tmp_path / "c.py"))
    await asyncio.sleep(0.1)
    assert fire_count == 2

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_watcher_coalesces_during_in_flight_reindex(tmp_path: Path) -> None:
    """AC-5: events arriving during a long-running reindex schedule
    exactly ONE follow-up reindex — burst events during the in-flight
    callback do not multiply."""
    from tests._fakes import FakeObserver
    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=20,
        observer_factory=lambda: fake,
    )

    in_flight = asyncio.Event()
    release = asyncio.Event()
    fire_count = 0

    async def _slow_on_change() -> None:
        nonlocal fire_count
        fire_count += 1
        in_flight.set()
        # Block here so the test can fire more events while we're "indexing".
        await release.wait()
        in_flight.clear()

    task = asyncio.create_task(fw.run_until_cancelled(_slow_on_change))
    await asyncio.sleep(0.01)

    # Trigger the first reindex.
    fake.fire(str(tmp_path / "a.py"))
    await asyncio.wait_for(in_flight.wait(), timeout=1.0)
    assert fire_count == 1

    # Now fire 5 more events while the first callback is still blocked.
    # Coalesce contract: these should schedule exactly ONE follow-up.
    for i in range(5):
        fake.fire(str(tmp_path / f"b{i}.py"))

    # Release the first reindex.
    release.set()
    # The follow-up should fire exactly once, then no more.
    await asyncio.sleep(0.15)
    assert fire_count == 2, (
        f"expected exactly 1 follow-up reindex; saw {fire_count - 1} extra"
    )

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_watcher_no_two_reindexes_overlap(tmp_path: Path) -> None:
    """Sibling pin to AC-5: two reindexes cannot run simultaneously."""
    from tests._fakes import FakeObserver
    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=10,
        observer_factory=lambda: fake,
    )

    overlap_observed = False
    active = 0

    async def _on_change() -> None:
        nonlocal active, overlap_observed
        active += 1
        if active > 1:
            overlap_observed = True
        await asyncio.sleep(0.05)
        active -= 1

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    await asyncio.sleep(0.01)

    fake.fire(str(tmp_path / "a.py"))
    await asyncio.sleep(0.02)
    fake.fire(str(tmp_path / "b.py"))
    await asyncio.sleep(0.2)

    assert not overlap_observed

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_index_db_wal_mode_enabled_for_concurrent_reindex(tmp_path: Path) -> None:
    """Risk R5: concurrent MCP queries + watcher-triggered reindex needs
    WAL mode so readers don't block on the reindex writer.

    The pin lives in `tests/test_db.py::test_wal_mode` already; this test
    re-asserts it with the watcher context attached so a future PR that
    naively removes WAL also breaks a `--watch`-flavored test, surfacing
    the impact on live reindex during MCP queries.
    """
    from pydocs_mcp.db import open_index_database

    db_path = tmp_path / "test.db"
    conn = open_index_database(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal", (
            f"watcher requires WAL so concurrent MCP queries during reindex "
            f"don't block; got journal_mode={mode!r}. "
            f"See spec §6 Risk R5."
        )
    finally:
        conn.close()
