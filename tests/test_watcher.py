"""Tests for the file watcher (`pydocs-mcp serve --watch`).

Mirrors spec §4.1 deliverable 6. The `FakeObserver` injected into
`FileWatcher` lets us drive events synchronously — no real `watchdog`
thread is involved, so tests stay fast and deterministic.
"""
from __future__ import annotations

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
