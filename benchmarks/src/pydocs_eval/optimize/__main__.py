"""The optimize CLI (spec §D5/§D7).

``python -m pydocs_eval.optimize --config <cfg>.yaml [--dry-run] [--resume LEDGER]``
drives one optimization run, or — with ``--dry-run`` — walks the WHOLE pipeline
spending nothing (``dry_run.dry_run``: seed firewall, ladder, split
determinism, the configured arms, adapter availability, and one full
orchestrator pass per arm on scripted doubles).

A real (non-dry) run is manual and preflight-gated (spec §D5): the CLI prints
the spend expectations, the per-arm plan and the runbook pointer, then stops
short of spending — a paid run needs an explicit go. No test drives the real
path — the whole suite runs ``--dry-run`` and spends nothing.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from pydocs_eval.optimize.dry_run import dry_run, print_real_run_expectations
from pydocs_eval.optimize.run_config import load_run_config


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pydocs_eval.optimize",
        description="Offline harness-artifact optimizer. Manual, preflight-gated, never CI.",
    )
    parser.add_argument("--config", type=Path, required=True, help="run config YAML (spec §D7)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="walk the whole pipeline with a fake runner/fitness; spend $0.00",
    )
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="resume from this trials ledger (already-scored candidates are skipped)",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("optimize_trials.jsonl"),
        help="JSONL trials ledger path (resume key: (fingerprint, split, objective, arm))",
    )
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    """Load the config, then dry-run or (real) print the plan + spend expectations."""
    cfg = load_run_config(args.config)
    ledger_path = args.resume if args.resume is not None else args.ledger
    if args.dry_run:
        return await dry_run(cfg, ledger_path=ledger_path)
    # Real path: machinery only in this slice — a paid run needs an explicit go
    # (spec spend gate). Print the plan + runbook pointer and stop short of
    # spending; a later, user-authorized change wires the real optimizer.
    print_real_run_expectations(cfg)
    print("Machinery is ready. Re-run with --dry-run to preflight, or authorize a paid run.")
    return 0


async def cli_main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run the optimize CLI; return the process exit code.

    Async so the whole pipeline (dataset drain, orchestrator pass) runs on one
    event loop. ``main()`` wraps this in ``asyncio.run`` for the console entry.
    """
    args = _build_arg_parser().parse_args(argv)
    return await _main_async(args)


def main() -> None:
    """Console entry: run the async CLI and exit with its code."""
    raise SystemExit(asyncio.run(cli_main()))


if __name__ == "__main__":  # pragma: no cover -- CLI entry, not unit-tested
    main()
