"""Grouped bar plots of one or more baseline JSON files.

Each baseline (the JSON shape produced by Task 6 of cleanups-and-pr-a, e.g.
``benchmarks/baselines/repoqa_snf.json``) becomes a colored bar group; each
metric (``recall@1`` / ``recall@5`` / ``recall@10`` / ``mrr`` / ``pass@1-needle``)
becomes an X-axis category. 95% CI error bars are rendered from the
``ci_low`` / ``ci_high`` fields the aggregator emits.

Designed for side-by-side comparison of different retrieval configurations
on the same dataset — today that's just one ``pydocs-mcp / baseline`` bar
group (BM25 over FTS5), but as PR-B3.1 lands dense embeddings + RRF the
same call site picks up additional bar groups automatically.

Color palette defaults to seaborn's ``colorblind`` — colorblind-safe AND
recommended by Nature for figure submissions, so plots produced here are
publication-ready out of the box.

Usage::

    from pathlib import Path
    from benchmarks.eval.plotting import plot_baselines

    fig = plot_baselines(
        baselines=[
            Path("benchmarks/baselines/repoqa_snf.json"),
            # Path("benchmarks/baselines/repoqa_snf_dense.json"),  # future
        ],
        metrics=("recall@1", "recall@5", "recall@10", "mrr"),
        output=Path("benchmarks/results/plots/repoqa_real.png"),
    )

Or from the command line::

    PYTHONPATH=benchmarks/src python -m benchmarks.eval.plotting \\
        benchmarks/baselines/repoqa_snf.json \\
        --output benchmarks/results/plots/repoqa_real.png \\
        --metrics recall@1,recall@5,recall@10,mrr
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path

# Import matplotlib BEFORE pyplot so a headless backend can be picked up
# via the MPLBACKEND env var (CI / tests set "Agg" to avoid the GUI
# bootstrap cost). Seaborn imports pyplot internally, so we order
# matplotlib first to honor MPLBACKEND consistently.
import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.container import BarContainer
from matplotlib.figure import Figure

from .baseline_record import BaselineRecord

# Single source of truth — referenced by both the public defaults and the
# CLI parser so bumping one updates the other (CLAUDE.md §"Default values").
_DEFAULT_PALETTE = "colorblind"
# Single source of truth for figure export resolution — previously the
# ``dpi=150`` literal was repeated in all three save blocks (CLAUDE.md
# §"Default values").
_SAVEFIG_DPI = 150
_DEFAULT_METRICS: tuple[str, ...] = (
    "recall@1",
    "recall@5",
    "recall@10",
    "mrr",
)
_DEFAULT_TIMING_METRICS: tuple[str, ...] = (
    "indexing_seconds",
    "search_seconds",
)
_DEFAULT_FIGSIZE: tuple[float, float] = (10.0, 6.0)
# One subplot per timing metric — height multiplies per metric so the
# total figure stays the right size for stacking 1, 2, or 3 panels.
_TIMING_PANEL_HEIGHT: float = 1.6  # inches per panel
_TIMING_BAR_PADDING: float = 0.5  # extra vertical padding for labels

# Pretty labels for the timing-metric panels — fall back to the raw
# metric name for unknown keys.
_TIMING_PRETTY: dict[str, str] = {
    "indexing_seconds": "Indexing time (seconds)",
    "search_seconds": "Per-query search latency (seconds)",
}


def plot_baselines(
    baselines: Iterable[BaselineRecord | Path],
    metrics: Iterable[str] = _DEFAULT_METRICS,
    *,
    output: Path | None = None,
    palette: str = _DEFAULT_PALETTE,
    title: str | None = None,
    figsize: tuple[float, float] = _DEFAULT_FIGSIZE,
) -> Figure:
    """Grouped vertical bar plot of one or more baselines.

    Args:
        baselines: each item is either a ``BaselineRecord`` or a ``Path`` to a
            baseline JSON (which is loaded into a record). At least one
            baseline is required.
        metrics: ordered metric names to plot on the X axis. Metrics missing
            from a given baseline are skipped silently for that baseline (so
            you can plot ``recall@10`` even if one baseline only reported
            ``recall@1``). Default: ``recall@1, recall@5, recall@10, mrr``.
        output: optional path to save the figure. Extension determines
            format (``.png``, ``.pdf``, ``.svg``). Parent dirs auto-created.
        palette: seaborn palette name. Default ``"colorblind"`` (Nature
            figure guidelines + colorblind-safe).
        title: optional figure title. Default: dataset name + tasks_ran of
            the first baseline.
        figsize: ``(width, height)`` in inches.

    Returns:
        The matplotlib ``Figure`` — caller may further customize, ``.show()``,
        or pass to a notebook display. Already saved to disk if ``output``
        was provided.

    Raises:
        ValueError: if ``baselines`` or ``metrics`` is empty.
    """
    records = _prepare(baselines, fn_name="plot_baselines", font_scale=1.1)

    metrics = tuple(metrics)
    if not metrics:
        raise ValueError("plot_baselines requires at least one metric")

    df = _long_dataframe(records, metrics)

    fig, ax = plt.subplots(figsize=figsize)

    system_order = [r.display_label for r in records]

    sns.barplot(
        data=df,
        x="metric",
        y="value",
        hue="system",
        hue_order=system_order,
        order=list(metrics),
        palette=palette,
        ax=ax,
        # We render our own CI bars from each record's ci_low / ci_high
        # values — seaborn's default `errorbar` would bootstrap the
        # mean column which is one point per system × metric.
        errorbar=None,
    )

    _overlay_ci_error_bars(ax, df, system_order, metrics)

    ax.set_xlabel("Metric")
    ax.set_ylabel("Score")
    ax.set_ylim(0.0, 1.0)

    if title is None:
        title = _default_title(records)
    ax.set_title(title)

    # Legend with compact provenance suffix per system.
    handles, labels = ax.get_legend_handles_labels()
    detailed = []
    for label in labels:
        rec = next(r for r in records if r.display_label == label)
        detailed.append(label + rec.legend_suffix)
    ax.legend(handles, detailed, title="System", loc="best", frameon=True)

    fig.tight_layout()

    _save_figure(fig, output)

    return fig


def _load_records(
    baselines: Iterable[BaselineRecord | Path],
    *,
    fn_name: str,
) -> list[BaselineRecord]:
    """Resolve a mix of ``BaselineRecord`` / ``Path`` items into records."""
    records = [
        item if isinstance(item, BaselineRecord) else BaselineRecord.from_path(item)
        for item in baselines
    ]
    if not records:
        raise ValueError(f"{fn_name} requires at least one baseline")
    return records


def _validate_same_dataset(
    records: list[BaselineRecord],
    *,
    fn_name: str,
) -> None:
    """Apples-to-apples guard — every baseline in a single plot must come
    from the same ``dataset`` slice. Cross-dataset overlays (e.g., the
    5-needle CI fixture next to the real 100-needle sweep) misrepresent
    the numbers because the fixture is a hermetic regression test, not a
    competing system. Caller can render multiple datasets by calling
    once per dataset and arranging the figures externally.
    """
    datasets = {r.dataset for r in records}
    if len(datasets) > 1:
        per_dataset = ", ".join(f"{r.display_label} → {r.dataset}" for r in records)
        raise ValueError(
            f"{fn_name} requires every baseline to come from the same "
            "dataset (apples-to-apples comparison). Got multiple "
            f"datasets: {sorted(datasets)}. Records: {per_dataset}. "
            f"Call {fn_name} once per dataset instead."
        )


def _prepare(
    baselines: Iterable[BaselineRecord | Path],
    *,
    fn_name: str,
    font_scale: float,
) -> list[BaselineRecord]:
    """Shared figure prelude: load records, apples-to-apples guard, theme.

    WHY one helper: all three plot functions repeated this block verbatim;
    a fourth plot type must not copy it a fourth time. Title placement and
    layout deliberately STAY in the callers — plot_timings uses
    ``fig.suptitle`` + constrained_layout while the others use
    ``ax.set_title`` + ``tight_layout``, so folding them here would change
    visual output. NOTE: the same-dataset guard now runs before the
    empty-metrics check in plot_baselines/plot_timings (error-precedence
    swap only; both inputs still raise ValueError).
    """
    records = _load_records(baselines, fn_name=fn_name)
    _validate_same_dataset(records, fn_name=fn_name)
    sns.set_theme(style="whitegrid", context="paper", font_scale=font_scale)
    return records


def _default_title(records: list[BaselineRecord]) -> str:
    """``"<dataset> (<tasks_ran> tasks)"`` from the first record."""
    return f"{records[0].dataset} ({records[0].tasks_ran} tasks)"


def _save_figure(fig: Figure, output: Path | None) -> None:
    """Save when an output path was given; parent dirs auto-created."""
    if output is None:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=_SAVEFIG_DPI, bbox_inches="tight")


def plot_timings(
    baselines: Iterable[BaselineRecord | Path],
    metrics: Iterable[str] = _DEFAULT_TIMING_METRICS,
    *,
    output: Path | None = None,
    palette: str = _DEFAULT_PALETTE,
    title: str | None = None,
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Horizontal bar plot of timing percentiles per baseline.

    Each metric (``indexing_seconds``, ``search_seconds``, ...) becomes
    its own subplot — indexing latency and per-query search latency live
    on very different magnitudes (seconds vs milliseconds), so stacking
    them on a shared X-axis would crush one of them. Inside each panel,
    one horizontal bar per baseline marks p50; a black line + cap
    extends to p95 to show tail dispersion. Exact p50 / p95 / p99
    numbers are annotated at the bar's right edge.

    Apples-to-apples constraint same as :func:`plot_baselines`: every
    baseline must come from the same ``dataset`` slice. Mixing the
    5-needle CI fixture next to the real 100-needle sweep raises
    ``ValueError``.

    Args:
        baselines: one or more ``BaselineRecord`` / ``Path`` items.
        metrics: ordered timing metric names — one subplot per metric.
            Default: ``("indexing_seconds", "search_seconds")``.
        output: optional figure save path.
        palette: seaborn palette name. Default ``"colorblind"``.
        title: figure title. Default: ``"<dataset> (<tasks_ran> tasks)"``
            from the first baseline.
        figsize: ``(width, height)``. Default sizes height per panel so
            adding metrics doesn't squish bars.

    Returns:
        The matplotlib ``Figure``.

    Raises:
        ValueError: if ``baselines`` or ``metrics`` is empty, or if the
            baselines span multiple datasets.
    """
    records = _prepare(baselines, fn_name="plot_timings", font_scale=1.0)
    metrics = tuple(metrics)
    if not metrics:
        raise ValueError("plot_timings requires at least one metric")

    if figsize is None:
        width = 10.0
        # Each panel gets a fixed slice of height; extra padding for the
        # global title + xlabel keeps the figure legible at 1, 2, or 3
        # metrics.
        height = (
            _TIMING_PANEL_HEIGHT * max(len(records), 1) * len(metrics) + _TIMING_BAR_PADDING * 2
        )
        figsize = (width, max(height, 3.0))

    fig, axes = plt.subplots(
        nrows=len(metrics),
        ncols=1,
        figsize=figsize,
        sharex=False,
        constrained_layout=True,
    )
    # plt.subplots returns a bare Axes when nrows=1 — normalize to list.
    axes_list = list(axes) if len(metrics) > 1 else [axes]

    colors = sns.color_palette(palette, n_colors=max(len(records), 3))

    for ax, metric in zip(axes_list, metrics):
        _plot_timing_axis(ax, records, metric, colors)

    if title is None:
        title = _default_title(records)
    fig.suptitle(title, fontsize=11, y=1.02)

    _save_figure(fig, output)

    return fig


def plot_metric_vs_latency(
    baselines: Iterable[BaselineRecord | Path],
    metric: str = "recall@10",
    *,
    latency_metric: str = "search_seconds",
    latency_percentile: str = "p50",
    output: Path | None = None,
    palette: str = _DEFAULT_PALETTE,
    title: str | None = None,
    figsize: tuple[float, float] = (10.0, 6.0),
) -> Figure:
    """Scatter plot of a principal score metric vs per-query search latency.

    Each baseline becomes one point — Y is a chosen score metric (default
    ``recall@10``), X is a chosen latency percentile (default ``p50`` of
    ``search_seconds``). Vertical error bars show the score's 95% CI
    (from ``ci_low`` / ``ci_high``); horizontal error bars extend the
    latency from the chosen percentile up to ``p95`` so tail-latency
    pressure is visible.

    Reading the chart:
    - Up-and-left = the dominant point (higher quality at lower latency).
    - Down-and-right = strictly worse.
    - Up-and-right = quality-vs-latency trade-off — typically the dense /
      hybrid retrievers vs BM25.

    Apples-to-apples constraint same as the other plot functions: every
    baseline must come from the same ``dataset`` slice.

    Args:
        baselines: one or more ``BaselineRecord`` / ``Path`` items.
        metric: Y-axis score metric. Default ``"recall@10"``.
        latency_metric: which latency series to use. Default
            ``"search_seconds"`` (use ``"indexing_seconds"`` for the
            indexing-cost-vs-quality trade-off instead).
        latency_percentile: ``"p50"`` / ``"p95"`` / ``"p99"``. Default
            ``"p50"``. The horizontal error bar always extends to ``p95``
            (or up to the chosen percentile when that exceeds p95) so
            the tail spread is visible.
        output: optional save path.
        palette: seaborn palette name.
        title: figure title. Default: ``"<dataset> (<tasks_ran> tasks)"``.
        figsize: ``(width, height)`` in inches.

    Returns:
        The matplotlib ``Figure``.

    Raises:
        ValueError: empty baselines, or mixed datasets.
    """
    records = _prepare(baselines, fn_name="plot_metric_vs_latency", font_scale=1.0)
    fig, ax = plt.subplots(figsize=figsize)

    colors = sns.color_palette(palette, n_colors=max(len(records), 3))

    plotted = 0
    for i, rec in enumerate(records):
        score_stats = rec.metrics.get(metric)
        latency_stats = rec.metrics.get(latency_metric)
        if not score_stats or not latency_stats:
            continue  # Skip baselines missing either axis.

        y = float(score_stats.get("mean", 0.0))
        y_lo = float(score_stats.get("ci_low", y))
        y_hi = float(score_stats.get("ci_high", y))

        # X in milliseconds — search latency is consistently sub-second so
        # ms is easier to read than 0.0xx-style seconds.
        x_target_s = float(latency_stats.get(latency_percentile, 0.0))
        x_p95_s = float(latency_stats.get("p95", x_target_s))
        x_ms = x_target_s * 1000.0
        x_hi_ms = max(x_p95_s * 1000.0, x_ms)

        label = rec.display_label + rec.legend_suffix

        ax.errorbar(
            x_ms,
            y,
            yerr=[[max(y - y_lo, 0.0)], [max(y_hi - y, 0.0)]],
            xerr=[[0.0], [max(x_hi_ms - x_ms, 0.0)]],
            fmt="o",
            color=colors[i],
            markersize=10,
            capsize=4,
            linewidth=1.2,
            label=label,
        )
        # Tiny offset so the label sits next to the point, not on top of it.
        ax.annotate(
            label,
            xy=(x_ms, y),
            xytext=(10, -4),
            textcoords="offset points",
            fontsize=8,
            color=colors[i],
        )
        plotted += 1

    if plotted == 0:
        raise ValueError(
            f"plot_metric_vs_latency: no baseline had both '{metric}' "
            f"and '{latency_metric}' populated."
        )

    ax.set_xlabel(
        f"Search latency {latency_percentile} (ms)  — horizontal bar extends to p95",
    )
    ax.set_ylabel(f"{metric}  (95% CI vertical bars)")
    ax.set_ylim(0.0, 1.0)
    ax.set_xlim(left=0.0)

    if title is None:
        title = _default_title(records)
    ax.set_title(title)

    # The annotations already identify each point — keep the legend off
    # by default so the chart stays clean when N is small. Users who
    # want a legend can grab handles/labels off the returned Figure.
    fig.tight_layout()

    _save_figure(fig, output)

    return fig


def _plot_timing_axis(
    ax,
    records: list[BaselineRecord],
    metric: str,
    colors: list,
) -> None:
    """Render one horizontal-bar timing panel.

    Bar length = p50. Black line + cap from p50 → p95. Annotation at the
    bar's right edge: ``p50 / p95 / p99`` formatted for legibility
    (µs / ms / s based on magnitude).
    """
    y_pos = list(range(len(records)))
    p50s: list[float] = []
    p95s: list[float] = []
    p99s: list[float] = []
    for rec in records:
        stats = rec.metrics.get(metric, {})
        p50 = float(stats.get("p50", 0.0))
        p95 = float(stats.get("p95", p50))
        p99 = float(stats.get("p99", p95))
        p50s.append(p50)
        p95s.append(p95)
        p99s.append(p99)

    ax.barh(
        y_pos,
        p50s,
        color=colors[: len(records)],
        edgecolor="black",
        linewidth=0.5,
        height=0.6,
    )

    # p50 → p95 whisker — drawn manually so the cap stays inside the bar
    # band regardless of how many panels share the figure.
    for i, (p50, p95) in enumerate(zip(p50s, p95s)):
        if p95 > p50:
            ax.plot([p50, p95], [i, i], color="black", linewidth=1.2)
            ax.plot(
                [p95, p95],
                [i - 0.12, i + 0.12],
                color="black",
                linewidth=1.2,
            )

    ax.set_yticks(y_pos)
    ax.set_yticklabels(
        [r.display_label + r.legend_suffix for r in records],
        fontsize=8,
    )
    ax.invert_yaxis()  # first record at top — natural reading order.

    pretty = _TIMING_PRETTY.get(metric, metric)
    ax.set_xlabel(f"{pretty}  (bar = p50, whisker = p95)", fontsize=9)

    # X-limit with headroom for the right-edge annotation.
    upper = max(p95s) if p95s else 0.0
    if upper > 0:
        ax.set_xlim(0.0, upper * 1.6)

    # Annotation: 'p50=… / p95=… / p99=…' next to the whisker end.
    for i, (p50, p95, p99) in enumerate(zip(p50s, p95s, p99s)):
        ax.text(
            (p95 if p95 > 0 else p50) + upper * 0.03,
            i,
            (
                f"p50={_format_seconds(p50)} / "
                f"p95={_format_seconds(p95)} / "
                f"p99={_format_seconds(p99)}"
            ),
            va="center",
            fontsize=8,
            family="monospace",
        )


def _format_seconds(value: float) -> str:
    """Format a duration in seconds at the right magnitude for the eye.

    < 1 ms → µs, < 1 s → ms, else seconds with two decimals.
    """
    if value <= 0:
        return "0s"
    if value < 1e-3:
        return f"{value * 1e6:.0f}µs"
    if value < 1.0:
        return f"{value * 1e3:.1f}ms"
    return f"{value:.2f}s"


def _long_dataframe(
    records: list[BaselineRecord],
    metrics: tuple[str, ...],
) -> pd.DataFrame:
    """Reshape records × metrics into a long-form DataFrame for seaborn."""
    rows: list[dict[str, float | str]] = []
    for rec in records:
        for metric in metrics:
            if metric not in rec.metrics:
                continue
            stats = rec.metrics[metric]
            mean = float(stats.get("mean", 0.0))
            rows.append(
                {
                    "system": rec.display_label,
                    "metric": metric,
                    "value": mean,
                    "ci_low": float(stats.get("ci_low", mean)),
                    "ci_high": float(stats.get("ci_high", mean)),
                }
            )
    return pd.DataFrame(rows)


def _overlay_ci_error_bars(
    ax,
    df: pd.DataFrame,
    system_order: list[str],
    metric_order: tuple[str, ...],
) -> None:
    """Draw 95% CI error bars on top of each grouped bar.

    seaborn's grouped barplot creates one ``BarContainer`` per hue value
    (system); the bars within a container are in ``order`` (metric) order.
    We pair each bar with the matching row in ``df`` to read its CI bounds.

    matplotlib 3.10+ also stores non-bar artists in ``ax.containers`` —
    filter to ``BarContainer`` instances only so the system index stays in
    sync with ``system_order``.
    """
    bar_containers = [c for c in ax.containers if isinstance(c, BarContainer)]
    for system_idx, container in enumerate(bar_containers):
        if system_idx >= len(system_order):
            break  # defensive — seaborn shouldn't emit extra bar groups
        system = system_order[system_idx]
        sys_df = df[df["system"] == system].set_index("metric").reindex(list(metric_order))
        for bar, (_, row) in zip(container, sys_df.iterrows()):
            if pd.isna(row["value"]):
                continue  # baseline missing this metric — skip the error bar
            mean = float(row["value"])
            lo = float(row["ci_low"])
            hi = float(row["ci_high"])
            ax.errorbar(
                bar.get_x() + bar.get_width() / 2.0,
                mean,
                yerr=[[max(mean - lo, 0.0)], [max(hi - mean, 0.0)]],
                fmt="none",
                ecolor="black",
                capsize=3,
                linewidth=1.0,
            )


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.eval.plotting",
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


if __name__ == "__main__":
    # Pre-pyplot backend selection — keeps CI / headless runs from
    # bootstrapping a GUI toolkit. Users with a display can override
    # via MPLBACKEND.
    if "MPLBACKEND" not in __import__("os").environ:
        matplotlib.use("Agg")
    raise SystemExit(_cli_main())


__all__ = (
    "BaselineRecord",
    "plot_baselines",
    "plot_metric_vs_latency",
    "plot_timings",
)
