# benchmarks/tests/eval/test_bench_cache.py
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.eval import _bench_cache


class _FakeConfig:
    # Stand-in for AppConfig: only compute_ingestion_pipeline_hash is read.
    def __init__(self, h: str) -> None:
        self._h = h

    def compute_ingestion_pipeline_hash(self) -> str:
        return self._h


def test_make_key_is_deterministic(tmp_path: Path) -> None:
    cfg = _FakeConfig("abc")
    k1 = _bench_cache.make_key(tmp_path, cfg)
    k2 = _bench_cache.make_key(tmp_path, cfg)
    assert k1 == k2
    assert len(k1) == 64  # sha256 hexdigest


def test_make_key_varies_with_corpus_and_ingestion(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert _bench_cache.make_key(a, _FakeConfig("h")) != _bench_cache.make_key(b, _FakeConfig("h"))
    assert _bench_cache.make_key(a, _FakeConfig("h1")) != _bench_cache.make_key(
        a, _FakeConfig("h2")
    )


def test_make_key_resolves_corpus_path(tmp_path: Path) -> None:
    # A relative-ish / unresolved path and its resolved form share a key.
    sub = tmp_path / "x"
    sub.mkdir()
    via_dotdot = tmp_path / "x" / ".." / "x"
    assert _bench_cache.make_key(sub, _FakeConfig("h")) == _bench_cache.make_key(
        via_dotdot, _FakeConfig("h")
    )


def test_enabled_flag_roundtrips() -> None:
    original = _bench_cache.is_enabled()
    try:
        _bench_cache.set_enabled(False)
        assert _bench_cache.is_enabled() is False
        _bench_cache.set_enabled(True)
        assert _bench_cache.is_enabled() is True
    finally:
        _bench_cache.set_enabled(original)


def test_lookup_miss_then_hit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    assert _bench_cache.lookup("deadbeef") is None
    # Simulate a built entry.
    d = _bench_cache.entry_dir("deadbeef")
    d.mkdir(parents=True)
    db = _bench_cache.db_path_for("deadbeef")
    db.write_text("not empty")
    assert _bench_cache.lookup("deadbeef") == db


def test_lookup_ignores_empty_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    d = _bench_cache.entry_dir("k")
    d.mkdir(parents=True)
    _bench_cache.db_path_for("k").touch()  # zero bytes
    assert _bench_cache.lookup("k") is None


def test_reserve_then_commit_promotes_atomically(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    build = _bench_cache.reserve("k")
    assert build.is_dir()
    # Write the db + a sidecar into the build dir.
    (build / "index.sqlite").write_text("db")
    (build / "index.plaid").write_text("plaid sidecar")
    db = _bench_cache.commit("k", build)
    assert db == _bench_cache.db_path_for("k")
    assert db.read_text() == "db"
    assert (_bench_cache.entry_dir("k") / "index.plaid").read_text() == "plaid sidecar"
    assert not build.exists()  # tmp consumed by the rename


def test_commit_loses_race_drops_tmp(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    # Pre-create the final entry (another process won).
    final = _bench_cache.entry_dir("k")
    final.mkdir(parents=True)
    _bench_cache.db_path_for("k").write_text("winner")
    build = _bench_cache.reserve("k")
    (build / "index.sqlite").write_text("loser")
    db = _bench_cache.commit("k", build)
    assert db.read_text() == "winner"  # winner kept
    assert not build.exists()  # loser dropped


def test_commit_lost_race_mid_window_uses_winner(tmp_path, monkeypatch) -> None:
    """TOCTOU: the other process promotes its entry BETWEEN commit's
    `final.exists()` check and its `build_dir.replace(final)` — the rename
    then hits a non-empty directory and raises ENOTEMPTY. The loser must
    drop its build dir and serve the winner's entry, exactly like the
    already-tested pre-check race — not crash the sweep leg and leak the
    pid-suffixed tmp dir."""
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    build = _bench_cache.reserve("k")
    (build / "index.sqlite").write_text("loser")

    real_replace = Path.replace

    def _racing_replace(self: Path, target):
        target_path = Path(target)
        if target_path == _bench_cache.entry_dir("k") and not target_path.exists():
            # Simulate the winner promoting inside the check-to-rename window.
            target_path.mkdir(parents=True)
            (target_path / "index.sqlite").write_text("winner")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _racing_replace)

    db = _bench_cache.commit("k", build)
    assert db == _bench_cache.db_path_for("k")
    assert db.read_text() == "winner"
    assert not build.exists(), "loser's build dir leaked"


def test_commit_reraises_replace_failure_without_a_winner(tmp_path, monkeypatch) -> None:
    """A rename failure with NO usable winner entry (cross-device link,
    permissions) is a real error — commit must re-raise instead of silently
    returning a path to a database that does not exist."""
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    build = _bench_cache.reserve("k")
    (build / "index.sqlite").write_text("data")

    def _broken_replace(self: Path, target):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(Path, "replace", _broken_replace)
    with pytest.raises(OSError):
        _bench_cache.commit("k", build)


def test_evict_removes_everything(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    d = _bench_cache.entry_dir("k")
    d.mkdir(parents=True)
    _bench_cache.db_path_for("k").write_text("db")
    removed = _bench_cache.evict()
    assert removed == 1
    assert not _bench_cache.cache_root().exists() or not any(_bench_cache.cache_root().iterdir())


def test_evict_empty_cache_is_zero(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    assert _bench_cache.evict() == 0


def test_info_lists_entries(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    d = _bench_cache.entry_dir("k")
    d.mkdir(parents=True)
    _bench_cache.db_path_for("k").write_text("db-bytes")
    rows = _bench_cache.info()
    assert len(rows) == 1
    assert rows[0]["key"] == "k"
    assert rows[0]["bytes"] >= len("db-bytes")
