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
