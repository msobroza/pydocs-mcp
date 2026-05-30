# benchmarks/tests/eval/test_runner_bench_cache_flag.py
from __future__ import annotations

from benchmarks.eval import _bench_cache
from benchmarks.eval.runner import _build_arg_parser


def test_flag_defaults_on() -> None:
    args = _build_arg_parser().parse_args(["--configs", "x.yaml"])
    assert args.bench_cache == "on"


def test_flag_accepts_off() -> None:
    args = _build_arg_parser().parse_args(["--configs", "x.yaml", "--bench-cache", "off"])
    assert args.bench_cache == "off"


def test_set_enabled_maps_off(monkeypatch) -> None:
    # Helper the runner uses to translate the flag into the module toggle.
    from benchmarks.eval.runner import _apply_bench_cache_flag

    original = _bench_cache.is_enabled()
    try:
        _apply_bench_cache_flag("off")
        assert _bench_cache.is_enabled() is False
        _apply_bench_cache_flag("on")
        assert _bench_cache.is_enabled() is True
    finally:
        _bench_cache.set_enabled(original)
