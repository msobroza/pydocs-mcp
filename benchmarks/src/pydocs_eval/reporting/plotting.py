"""Facade over the per-figure plot modules + the plotting CLI.

The three figure renderers live one-per-module (``plot_baselines`` /
``plot_timings`` / ``plot_metric_vs_latency``); this module re-exports them
so ``from pydocs_eval.reporting.plotting import plot_baselines`` keeps
working as the single import point, and owns the command-line interface.

Color palette defaults to seaborn's ``colorblind`` — colorblind-safe AND
recommended by Nature for figure submissions, so plots produced here are
publication-ready out of the box.

Usage::

    from pathlib import Path
    from pydocs_eval.reporting.plotting import plot_baselines

    fig = plot_baselines(
        baselines=[
            Path("benchmarks/baselines/repoqa_snf.json"),
            # add further baseline JSONs here for side-by-side bar groups
        ],
        metrics=("recall@1", "recall@5", "recall@10", "mrr"),
        output=Path("benchmarks/results/plots/repoqa_real.png"),
    )

Or from the command line::

    PYTHONPATH=benchmarks/src python -m pydocs_eval.reporting.plotting \\
        benchmarks/baselines/repoqa_snf.json \\
        --output benchmarks/results/plots/repoqa_real.png \\
        --metrics recall@1,recall@5,recall@10,mrr
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

# Import matplotlib BEFORE pyplot so a headless backend can be picked up
# via the MPLBACKEND env var (CI / tests set "Agg" to avoid the GUI
# bootstrap cost). Seaborn imports pyplot internally, so we order
# matplotlib first to honor MPLBACKEND consistently.
import matplotlib
import matplotlib.pyplot as plt

from ._plot_common import _DEFAULT_METRICS, _DEFAULT_PALETTE, _DEFAULT_TIMING_METRICS
from .baseline_record import BaselineRecord
from .plot_baselines import plot_baselines
from .plot_metric_vs_latency import plot_metric_vs_latency
from .plot_timings import plot_timings


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pydocs_eval.reporting.plotting",
        description=(
            "Plot baseline JSON files. Default mode: grouped vertical "
            "bars per metric (score metrics like recall@k / MRR) with "
            "95%% CI error bars. ``--timings`` switches to horizontal "
            "bars over timing percentiles (indexing_seconds / "
            "search_seconds) — one subplot per metric, with a p50 bar "
            "and p95 whisker."
        ),
    )
    parser.add_argument(
        "baselines",
        nargs="+",
        type=Path,
        help="One or more baseline JSON paths (e.g. benchmarks/baselines/repoqa_snf.json).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        help="Output path for the figure (.png, .pdf, .svg).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--timings",
        action="store_const",
        dest="mode",
        const="timings",
        help=(
            "Timing-percentile mode (horizontal bars, p50 + p95 whisker, "
            "one subplot per timing metric). Default metrics become "
            "indexing_seconds + search_seconds."
        ),
    )
    mode.add_argument(
        "--scatter",
        action="store_const",
        dest="mode",
        const="scatter",
        help=(
            "Scatter mode — quality vs latency. One point per baseline; "
            "Y = --scatter-metric (default recall@10), X = "
            "--scatter-latency p50 (default search_seconds, ms). "
            "Vertical bars = 95%% score CI; horizontal bar extends to p95 "
            "latency."
        ),
    )
    parser.set_defaults(mode="scores")
    parser.add_argument(
        "--metrics",
        type=str,
        default=None,
        help=(
            "Comma-separated metric names (scores or timings mode). "
            f"Default scores: {','.join(_DEFAULT_METRICS)}. "
            f"Default timings: {','.join(_DEFAULT_TIMING_METRICS)}."
        ),
    )
    parser.add_argument(
        "--scatter-metric",
        type=str,
        default="recall@10",
        help="Score metric for the scatter Y-axis. Default: recall@10.",
    )
    parser.add_argument(
        "--scatter-latency",
        type=str,
        default="search_seconds",
        help=(
            "Latency metric for the scatter X-axis. Default: "
            "search_seconds. Use indexing_seconds for an indexing-cost "
            "trade-off chart instead."
        ),
    )
    parser.add_argument(
        "--scatter-percentile",
        type=str,
        default="p50",
        choices=("p50", "p95", "p99"),
        help="Latency percentile for the scatter X-axis. Default: p50.",
    )
    parser.add_argument(
        "--palette",
        type=str,
        default=_DEFAULT_PALETTE,
        help=f"seaborn palette name. Default: {_DEFAULT_PALETTE}",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Figure title. Default: '<dataset> (<tasks_ran> tasks)' from first baseline.",
    )
    return parser


def _cli_main(argv: list[str] | None = None) -> int:
    args = _cli_parser().parse_args(argv)

    if args.mode == "scatter":
        fig = plot_metric_vs_latency(
            baselines=args.baselines,
            metric=args.scatter_metric,
            latency_metric=args.scatter_latency,
            latency_percentile=args.scatter_percentile,
            output=args.output,
            palette=args.palette,
            title=args.title,
        )
    else:
        default_metrics = _DEFAULT_TIMING_METRICS if args.mode == "timings" else _DEFAULT_METRICS
        if args.metrics:
            metrics = tuple(m.strip() for m in args.metrics.split(",") if m.strip())
        else:
            metrics = default_metrics
        plot_fn = plot_timings if args.mode == "timings" else plot_baselines
        fig = plot_fn(
            baselines=args.baselines,
            metrics=metrics,
            output=args.output,
            palette=args.palette,
            title=args.title,
        )
    plt.close(fig)
    print(f"Saved {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Console-script / ``python -m`` entry.

    Pre-pyplot backend selection — keeps CI / headless runs from
    bootstrapping a GUI toolkit. Users with a display can override via
    MPLBACKEND. (``matplotlib.use`` after the pyplot import above is
    still honored — it switches the active backend.)
    """
    if "MPLBACKEND" not in os.environ:
        matplotlib.use("Agg")
    return _cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "BaselineRecord",
    "plot_baselines",
    "plot_metric_vs_latency",
    "plot_timings",
)
