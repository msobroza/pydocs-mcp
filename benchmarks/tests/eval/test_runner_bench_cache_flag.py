# benchmarks/tests/eval/test_runner_bench_cache_flag.py
from __future__ import annotations

from pydocs_eval import _bench_cache
from pydocs_eval.runner import _build_arg_parser


def test_flag_defaults_on() -> None:
    args = _build_arg_parser().parse_args(["--configs", "x.yaml"])
    assert args.bench_cache == "on"


def test_flag_accepts_off() -> None:
    args = _build_arg_parser().parse_args(["--configs", "x.yaml", "--bench-cache", "off"])
    assert args.bench_cache == "off"


def test_cleanup_flag_defaults_false() -> None:
    args = _build_arg_parser().parse_args(["--configs", "x.yaml"])
    assert args.bench_cache_cleanup is False


def test_cleanup_flag_sets_true() -> None:
    args = _build_arg_parser().parse_args(["--configs", "x.yaml", "--bench-cache-cleanup"])
    assert args.bench_cache_cleanup is True


def test_maybe_cleanup_evicts_when_enabled(tmp_path, monkeypatch) -> None:
    from pydocs_eval.runner import _maybe_cleanup_bench_cache

    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    d = _bench_cache.entry_dir("k")
    d.mkdir(parents=True)
    _bench_cache.db_path_for("k").write_text("db")

    _maybe_cleanup_bench_cache(enabled=True)
    assert _bench_cache.lookup("k") is None  # wiped


def test_maybe_cleanup_noop_when_disabled(tmp_path, monkeypatch) -> None:
    from pydocs_eval.runner import _maybe_cleanup_bench_cache

    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    d = _bench_cache.entry_dir("k")
    d.mkdir(parents=True)
    _bench_cache.db_path_for("k").write_text("db")

    _maybe_cleanup_bench_cache(enabled=False)
    assert _bench_cache.lookup("k") is not None  # untouched
