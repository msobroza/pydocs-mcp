# benchmarks/tests/eval/test_bench_cache_cli.py
from __future__ import annotations

from benchmarks.eval import _bench_cache
from benchmarks.eval.bench_cache import main


def test_cli_info_empty(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    rc = main(["info"])
    assert rc == 0
    assert "0 entries" in capsys.readouterr().out


def test_cli_evict(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    d = _bench_cache.entry_dir("k")
    d.mkdir(parents=True)
    _bench_cache.db_path_for("k").write_text("db")
    rc = main(["evict"])
    assert rc == 0
    assert "evicted 1" in capsys.readouterr().out.lower()
    assert _bench_cache.lookup("k") is None
