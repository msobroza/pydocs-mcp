# benchmarks/src/benchmarks/eval/bench_cache.py
"""CLI for the benchmark index cache: `python -m benchmarks.eval.bench_cache {info,evict}`."""

from __future__ import annotations

import argparse
import datetime as _dt

from . import _bench_cache


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.eval.bench_cache")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="list cached indexed DBs (read-only)")
    sub.add_parser("evict", help="remove every cached indexed DB")
    args = parser.parse_args(argv)

    if args.cmd == "info":
        rows = _bench_cache.info()
        print(f"{_bench_cache.cache_root()}: {len(rows)} entries")
        for r in rows:
            mb = int(r["bytes"]) / 1_048_576
            ts = _dt.datetime.fromtimestamp(float(r["mtime"])).isoformat(timespec="seconds")
            print(f"  {str(r['key'])[:12]}  {mb:7.1f} MB  {ts}")
        return 0

    if args.cmd == "evict":
        n = _bench_cache.evict()
        print(f"evicted {n} cache entr{'y' if n == 1 else 'ies'} from {_bench_cache.cache_root()}")
        return 0

    return 1  # unreachable: subparser is required


if __name__ == "__main__":
    raise SystemExit(main())
