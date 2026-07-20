"""CLI: ``pydocs-eval-optimizer-preflight`` — the standing no-spend loop health check.

Runs the full ADR 0018 §2 dry-run (mutation → validity → render+hash → canned
rollout → derived record → gate → ledger entry) and prints the health report. This
is the STANDING PRECONDITION for any paid candidate evaluation: exit 0 only when
the loop renders ``HEALTHY``.

``--rollout-dir`` overrides the offline widgetlib fixture (a directory carrying
``events.jsonl`` + ``facts.json``); ``--workspace`` roots the candidate ledger.

Exit codes:
    0 — the loop is HEALTHY.
    1 — a seam is BROKEN (invalid mutation, over-budget gate, or ledger failure).
    2 — an input error (missing rollout fixture / unreadable facts).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from pydocs_eval.optimize.preflight.health_check import (
    PreflightResult,
    default_rollout_dir,
    run_preflight,
)
from pydocs_eval.optimize.preflight.report import render_preflight_report
from pydocs_eval.trajectory.compute_metrics_cli import ComputeMetricsError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pydocs-eval-optimizer-preflight",
        description="No-spend optimizer loop health check (ADR 0018 §2 precondition gate).",
    )
    parser.add_argument(
        "--rollout-dir",
        type=Path,
        default=None,
        help="trajectory dir (events.jsonl + facts.json); default: committed widgetlib fixture",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="root for the candidate ledger (default: a fresh temp dir)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``pydocs-eval-optimizer-preflight`` console script."""
    args = _build_parser().parse_args(argv)
    try:
        result = _run(args)
    except (FileNotFoundError, ComputeMetricsError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(render_preflight_report(result), end="")
    return 0 if result.ok else 1


def _run(args: argparse.Namespace) -> PreflightResult:
    rollout_dir = args.rollout_dir or default_rollout_dir()
    workspace = args.workspace or Path(tempfile.mkdtemp(prefix="preflight-"))
    return run_preflight(rollout_fn=lambda: rollout_dir, workspace=workspace)


if __name__ == "__main__":  # pragma: no cover - module-run convenience
    sys.exit(main())
