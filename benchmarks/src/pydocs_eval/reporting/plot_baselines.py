"""Grouped bar plot of one or more baseline JSON files (score metrics).

Each baseline (the aggregate JSON shape the eval runner emits, e.g.
``benchmarks/baselines/repoqa_snf.json``) becomes a colored bar group; each
metric (``recall@1`` / ``recall@5`` / ``recall@10`` / ``mrr`` / ``pass@1-needle``)
becomes an X-axis category. 95% CI error bars are rendered from the
``ci_low`` / ``ci_high`` fields the aggregator emits.

Designed for side-by-side comparison of different retrieval configurations
on the same dataset — pass one baseline JSON per configuration (BM25, dense,
hybrid, ...) and the same call site picks up additional bar groups
automatically.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib.container import BarContainer
from matplotlib.figure import Figure

from ._plot_common import (
    _DEFAULT_FIGSIZE,
    _DEFAULT_METRICS,
    _DEFAULT_PALETTE,
    _default_title,
    _prepare,
    _save_figure,
)
from .baseline_record import BaselineRecord


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


__all__ = ("plot_baselines",)
