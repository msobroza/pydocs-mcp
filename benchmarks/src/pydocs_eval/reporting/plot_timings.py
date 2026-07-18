"""Horizontal bar plot of timing percentiles per baseline.

One subplot per timing metric (``indexing_seconds`` / ``search_seconds``) so
indexing latency (seconds) and per-query search latency (milliseconds) don't
get crushed onto a shared axis. Inside each panel, one horizontal bar per
baseline marks p50; a black line + cap extends to p95; the exact
p50 / p95 / p99 triple is annotated at the bar's right edge.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.figure import Figure

from ._plot_common import (
    _DEFAULT_PALETTE,
    _DEFAULT_TIMING_METRICS,
    _default_title,
    _prepare,
    _save_figure,
)
from .baseline_record import BaselineRecord

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


__all__ = ("plot_timings",)
