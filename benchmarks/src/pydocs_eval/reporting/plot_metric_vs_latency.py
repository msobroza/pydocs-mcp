"""Scatter plot of a principal score metric vs per-query search latency.

Each baseline becomes one point — Y is a chosen score metric (default
``recall@10``), X is a chosen latency percentile (default ``p50`` of
``search_seconds``). Up-and-left is the dominant corner: higher quality at
lower latency.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.figure import Figure

from ._plot_common import (
    _DEFAULT_PALETTE,
    _default_title,
    _prepare,
    _save_figure,
)
from .baseline_record import BaselineRecord


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


__all__ = ("plot_metric_vs_latency",)
