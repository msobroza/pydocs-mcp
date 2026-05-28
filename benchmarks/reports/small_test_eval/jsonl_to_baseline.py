#!/usr/bin/env python3
"""Convert a single ``jsonl_tracker`` run file into a baseline JSON
``plotting.BaselineRecord.from_path`` can read.

The jsonl tracker writes one line per event: ``run_start`` (system /
config / dataset / tags), one ``metric`` line per metric (per-task with
``step=<int>``, aggregated with ``step=None``), and a final ``run_end``.

This script reads the aggregates (``step is None``, names ending in
``_mean`` / ``_ci_low`` / ``_ci_high`` / ``_p50`` / ``_p95`` / ``_p99``)
and emits the shape expected by ``plotting.py`` — see
``benchmarks/baselines/repoqa_snf.json`` for the canonical example.

Usage::

    python jsonl_to_baseline.py <run.jsonl> -o <out.json> --label "<label>"

The script is intentionally self-contained (stdlib only) so it can be
run from the report directory without ``benchmarks`` on the Python path.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

_AGG_SUFFIXES = ("_mean", "_ci_low", "_ci_high", "_p50", "_p95", "_p99")


def _strip_suffix(name: str) -> tuple[str, str] | None:
    """``recall@10_mean`` → ``("recall@10", "mean")``. None if no match."""
    for suf in _AGG_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)], suf.lstrip("_")
    return None


def jsonl_to_baseline(jsonl_path: Path, *, label: str) -> dict:
    """Read a jsonl_tracker run file and return a baseline-JSON dict."""
    metrics: dict[str, dict[str, float]] = defaultdict(dict)
    run_start: dict | None = None
    per_task_steps: set[int] = set()

    with jsonl_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ev = rec.get("_event")
            if ev == "run_start":
                run_start = rec
            elif ev == "metric":
                name = rec["name"]
                value = rec["value"]
                step = rec.get("step")
                if step is None:
                    parsed = _strip_suffix(name)
                    if parsed is None:
                        # Skip non-aggregate keys we don't know how to fold.
                        continue
                    base, agg = parsed
                    metrics[base][agg] = value
                else:
                    # Per-task: count distinct steps for tasks_ran.
                    if isinstance(step, int):
                        per_task_steps.add(step)

    if run_start is None:
        raise ValueError(f"{jsonl_path}: missing run_start event")

    tags = run_start.get("tags", {})
    return {
        "dataset": run_start["dataset"],
        "system": run_start["system"],
        "config": run_start["config_name"],
        "tasks_ran": len(per_task_steps),
        "metrics": dict(metrics),
        "captured_at": run_start.get("ts"),
        "git_sha": tags.get("git_sha", ""),
        "source_jsonl": str(jsonl_path),
        "label": label,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jsonl_to_baseline.py",
        description="Distill a jsonl_tracker run file into a plotting baseline JSON.",
    )
    parser.add_argument("jsonl", type=Path, help="run-level .jsonl produced by jsonl_tracker")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="output .json path",
    )
    parser.add_argument(
        "--label",
        type=str,
        required=True,
        help="human-readable label embedded in the legend (e.g. 'small_test-30-tasks')",
    )
    args = parser.parse_args(argv)

    if not args.jsonl.is_file():
        print(f"jsonl not a file: {args.jsonl}", file=sys.stderr)
        return 2

    baseline = jsonl_to_baseline(args.jsonl, label=args.label)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(baseline, indent=2) + "\n")
    print(f"wrote {args.output} (tasks_ran={baseline['tasks_ran']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
