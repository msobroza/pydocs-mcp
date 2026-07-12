"""Tests for the file watcher (`pydocs-mcp serve --watch`).

Mirrors spec §4.1 deliverable 6. The `FakeObserver` injected into
`FileWatcher` lets us drive events synchronously — no real `watchdog`
thread is involved, so tests stay fast and deterministic.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def test_watcher_module_importable() -> None:
    """The module itself imports without watchdog installed.

    `watchdog` import lives inside `FileWatcher.__post_init__` /
    constructor only, so users who never touch `--watch` pay zero cost.
    """
    from pydocs_mcp.serve import watcher


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
    without filesystem timing nondeterminism. Injection goes through
    `handler.dispatch(event)` — the entrypoint the real watchdog
    Observer calls; the fake must not be more lenient than watchdog."""
    from tests._fakes import FakeObserver

    obs = FakeObserver()
    assert not obs.started
    obs.start()
    assert obs.started

    fired: list[str] = []

    class _Handler:
        def dispatch(self, event):
            fired.append(event.src_path)

    obs.schedule(_Handler(), "/tmp/some/dir", recursive=True)
    obs.fire("/tmp/some/dir/file.py")
    assert fired == ["/tmp/some/dir/file.py"]

    obs.stop()
    assert not obs.started
    obs.join()  # idempotent no-op


def test_fake_observer_rejects_handler_without_dispatch() -> None:
    """A handler exposing only `on_any_event` dies with AttributeError in
    the REAL watchdog emitter thread (`BaseObserver.dispatch_events` calls
    `handler.dispatch(event)`). The fake enforces the same contract so a
    dispatch-less handler can never pass unit tests while being broken in
    production."""
    import pytest

    from tests._fakes import FakeObserver

    obs = FakeObserver()
    obs.start()

    class _OnAnyEventOnly:
        def on_any_event(self, event):  # pragma: no cover — must not be called
            raise AssertionError("real watchdog never calls on_any_event directly")

    obs.schedule(_OnAnyEventOnly(), "/x", recursive=True)
    with pytest.raises(AttributeError):
        obs.fire("/x/a.py")


def test_fake_observer_fire_event_has_src_and_dest_path_attrs() -> None:
    """`fire(path)` synthesizes an event with the `src_path` / `dest_path`
    attrs the watchdog handler expects (mirrors
    `watchdog.events.FileSystemEvent`, whose non-move events default
    `dest_path` to '')."""
    from tests._fakes import FakeObserver

    obs = FakeObserver()
    obs.start()
    captured: list[object] = []

    class _Handler:
        def dispatch(self, event):
            captured.append(event)

    obs.schedule(_Handler(), "/x", recursive=True)
    obs.fire("/x/a.py")
    assert captured[0].src_path == "/x/a.py"
    assert captured[0].dest_path == ""

    obs.fire_moved("/x/a.py.tmp", "/x/a.py")
    assert captured[1].src_path == "/x/a.py.tmp"
    assert captured[1].dest_path == "/x/a.py"


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
    fake.fire(str(tmp_path / "x.pyc"))  # bad extension
    fake.fire(str(tmp_path / "__pycache__" / "x.cpython.pyc"))  # ignore
    fake.fire(str(tmp_path / ".git" / "HEAD"))  # ignore
    fake.fire(str(tmp_path / "x.png"))  # bad ext

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


def test_matches_normalizes_uppercase_configured_extension(tmp_path: Path) -> None:
    """Regression test: a user overlay configuring `extensions: ['.PY']`
    (uppercase) must still match `.py` files.

    `_matches` lowercases the FILE's suffix (`path.suffix.lower()`) but,
    before this fix, never normalized the CONFIGURED `extensions` tuple —
    `'.py' not in ('.PY',)` is True, so `_matches` silently returned False
    for every source file and only manifests (`pyproject.toml` /
    `requirements*.txt`) still fired. Normalizing at construction
    (`__post_init__`) closes the gap symmetrically with the file-suffix
    case-insensitivity already documented on `_matches`.
    """
    from pydocs_mcp.serve.watcher import FileWatcher

    fw = FileWatcher(
        root=tmp_path,
        extensions=(".PY",),
        ignore_globs=(),
        debounce_ms=10,
        observer_factory=lambda: object(),
    )
    assert fw._matches(tmp_path / "app.py") is True


def test_matches_normalizes_configured_extension_missing_leading_dot(
    tmp_path: Path,
) -> None:
    """Regression test: a user overlay configuring `extensions: ['py']`
    (no leading dot) must still match `.py` files — `path.suffix` always
    includes the dot, so an un-dotted configured extension can never
    match without normalization at construction time."""
    from pydocs_mcp.serve.watcher import FileWatcher

    fw = FileWatcher(
        root=tmp_path,
        extensions=("py",),
        ignore_globs=(),
        debounce_ms=10,
        observer_factory=lambda: object(),
    )
    assert fw._matches(tmp_path / "app.py") is True


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
        f"callback fired at {elapsed * 1000:.1f}ms; expected ~30ms debounce"
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
    assert fire_count == 2, f"expected exactly 1 follow-up reindex; saw {fire_count - 1} extra"

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


async def test_cancelling_run_until_cancelled_waits_for_in_flight_reindex(
    tmp_path: Path,
) -> None:
    """Regression: cancelling `run_until_cancelled` while a `watcher-trigger`
    background task is mid-reindex must not leave that task unsupervised.

    Before the fix, `_consume`'s `finally` (`observer.stop()` / `.join()`)
    said nothing about `bg_tasks` — the fire-and-forget `_trigger_with_followup`
    task kept running (and writing) after `run_until_cancelled` had already
    returned to its caller. The contract this test pins: by the time the
    cancelled `run_until_cancelled` task is awaited, any in-flight trigger task
    has been cancelled and awaited — `on_change` sees a `CancelledError` at its
    current await point, not a silent orphan run.
    """
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

    in_flight = asyncio.Event()
    on_change_cancelled = False

    async def _blocking_on_change() -> None:
        nonlocal on_change_cancelled
        in_flight.set()
        try:
            # Blocks "mid-reindex" until the watcher task is cancelled.
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            on_change_cancelled = True
            raise

    task = asyncio.create_task(fw.run_until_cancelled(_blocking_on_change))
    await asyncio.sleep(0.01)

    fake.fire(str(tmp_path / "a.py"))
    # Wait until the fire-and-forget "watcher-trigger" task is actually
    # inside on_change (i.e. the reindex is in flight), not just queued.
    await asyncio.wait_for(in_flight.wait(), timeout=1.0)

    # Cancel + await the outer task exactly as server shutdown does
    # (`_run_watch_loop`'s finally in `__main__.py`).
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        pass

    # The defined contract: the in-flight reindex was cancelled (not left
    # running unsupervised past the point `run_until_cancelled` returned).
    assert on_change_cancelled, (
        "in-flight 'watcher-trigger' task was not cancelled/awaited before "
        "run_until_cancelled returned — it is running unsupervised"
    )


async def test_watcher_survives_on_change_exception_and_drains_deferred_paths(
    tmp_path: Path,
) -> None:
    """Regression: `on_change` raising mid-flight must not strand paths that
    arrived (and were appended to `deferred_paths`) during that failed call.

    Trigger A holds `reindex_lock` and its `on_change()` raises (e.g. a
    transient sqlite "database is locked"). Trigger B arrives while A is in
    flight and appends its paths to `deferred_paths`. Before the fix, the
    exception propagated out of `_trigger_with_followup`, skipping the
    `while deferred_paths:` drain — B's paths sat unindexed until some
    unrelated future event arrived (in an idle workspace: lost forever).

    Pins two things `on_change` raising must NOT break: (1) B's batch still
    drains and fires a follow-up without a third filesystem event; (2) the
    consumer loop stays alive to keep handling later events.
    """
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
    calls: list[list[str]] = []
    first_call = True

    async def _flaky_on_change() -> None:
        nonlocal first_call
        calls.append([])
        in_flight.set()
        # Block here so the test can fire trigger B while A is in flight.
        await release.wait()
        if first_call:
            first_call = False
            raise RuntimeError("database is locked")

    task = asyncio.create_task(fw.run_until_cancelled(_flaky_on_change))
    await asyncio.sleep(0.01)

    # Trigger A: acquires reindex_lock, blocks inside on_change.
    fake.fire(str(tmp_path / "a.py"))
    await asyncio.wait_for(in_flight.wait(), timeout=1.0)
    assert len(calls) == 1

    # Trigger B arrives while A is in flight -> appended to deferred_paths.
    in_flight.clear()
    fake.fire(str(tmp_path / "b.py"))
    await asyncio.sleep(0.05)  # let debounce collapse + queue the deferred append

    # Release A: on_change raises. Without the fix, this aborts
    # _trigger_with_followup before the `while deferred_paths:` drain,
    # so B's follow-up never fires absent a THIRD filesystem event.
    release.set()

    # B's follow-up must fire on its own — no third event injected.
    await asyncio.wait_for(in_flight.wait(), timeout=1.0)
    assert len(calls) == 2, (
        "on_change raising stranded the deferred batch; expected a "
        "follow-up reindex for trigger B without a third filesystem event"
    )

    # The watcher task must still be alive (no unhandled task exception).
    assert not task.done(), "watcher task died after on_change raised"

    # Let the second (successful) on_change call return cleanly.
    release.set()
    await asyncio.sleep(0.05)
    assert not task.done()

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
