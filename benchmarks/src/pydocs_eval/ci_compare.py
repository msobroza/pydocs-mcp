"""Tiny CI helper: compare a metric in a current JSONL run vs a baseline JSON.

Exits non-zero if the metric mean dropped by more than ``--threshold`` below
baseline. A flat percentage-point threshold is used (not the bootstrap CI)
because a 5-task fixture run has a CI band wider than any realistic
regression we'd want to catch.

Exit codes:
    0 — metric mean is at-or-above (baseline - threshold).
    1 — regression detected; metric mean dropped > threshold below baseline.
    2 — input error (missing JSONL files, missing metric in JSONL).
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

from .baseline_record import BaselineRecord


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pydocs_eval.ci_compare",
        description="Compare current benchmark JSONL against a baseline JSON.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="path to baseline JSON file (e.g. benchmarks/baselines/repoqa_snf.json)",
    )
    parser.add_argument(
        "--current",
        required=True,
        help="glob pattern for current-run JSONL files (most-recent is read)",
    )
    parser.add_argument(
        "--metric",
        required=True,
        help="metric name to compare (e.g. recall@10)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.02,
        help="absolute drop threshold (default 0.02 = 2 percentage points)",
    )
    args = parser.parse_args()

    # WHY BaselineRecord: the single read model for baseline JSONs
    # (stdlib-only module) instead of re-encoding the JSON shape here. ANY
    # unreadable baseline is an input error → exit 2: missing file
    # (FileNotFoundError ⊂ OSError), corrupt JSON (JSONDecodeError ⊂
    # ValueError), missing top-level field or metric (KeyError). Letting
    # these escape as tracebacks made the interpreter exit 1 — the
    # "regression detected" code — misclassifying input errors.
    try:
        baseline = BaselineRecord.from_path(args.baseline)
        baseline_mean = baseline.metrics[args.metric]["mean"]
    except (OSError, ValueError, KeyError) as exc:
        print(
            f"::error::Cannot read metric {args.metric!r} from baseline {args.baseline}: {exc!r}",
            file=sys.stderr,
        )
        return 2

    jsonl_files = glob.glob(args.current)
    if not jsonl_files:
        print(f"::error::No JSONL files matched {args.current}", file=sys.stderr)
        return 2

    # WHY: pick the most-recently-modified file so re-running the CI step
    # against an existing results dir compares the latest run, not whichever
    # file glob.glob returned first.
    latest = max(jsonl_files, key=lambda p: Path(p).stat().st_mtime)
    current_mean: float | None = None
    for line in Path(latest).read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError as exc:
            print(f"::error::Corrupt JSONL line in {latest}: {exc!r}", file=sys.stderr)
            return 2
        if rec.get("_event") == "metric" and rec.get("name") == f"{args.metric}_mean":
            current_mean = float(rec["value"])

    if current_mean is None:
        print(
            f"::error::Could not find {args.metric}_mean in {latest}",
            file=sys.stderr,
        )
        return 2

    delta = current_mean - baseline_mean
    if delta < -args.threshold:
        print(
            f"::error::{args.metric} dropped {-delta * 100:.1f}pp below baseline "
            f"({baseline_mean * 100:.1f}% → {current_mean * 100:.1f}%)",
        )
        return 1
    print(
        f"OK: {args.metric} = {current_mean * 100:.1f}% "
        f"(baseline {baseline_mean * 100:.1f}%, Δ={delta * 100:+.1f}pp)",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover -- CLI entry, not unit-tested
    sys.exit(main())
