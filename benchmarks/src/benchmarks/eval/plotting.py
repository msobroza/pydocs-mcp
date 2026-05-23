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
import json
from collections.abc import Iterable
from dataclasses import dataclass
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

# Single source of truth — referenced by both the public defaults and the
# CLI parser so bumping one updates the other (CLAUDE.md §"Default values").
_DEFAULT_PALETTE = "colorblind"
_DEFAULT_METRICS: tuple[str, ...] = (
    "recall@1", "recall@5", "recall@10", "mrr",
)
_DEFAULT_FIGSIZE: tuple[float, float] = (10.0, 6.0)


@dataclass(frozen=True, slots=True)
class BaselineRecord:
    """A loaded baseline JSON ready for plotting.

    Mirrors the shape written by Task 6's heredoc in
    ``benchmarks/baselines/*.json``: ``dataset``, ``system``, ``config``,
    ``label``, ``tasks_ran``, ``metrics``, ``captured_at``, ``git_sha``,
    ``source_jsonl``.
    """

    system: str
    config: str
    label: str
    dataset: str
    tasks_ran: int
    metrics: dict[str, dict[str, float]]
    captured_at: str | None
    git_sha: str | None

    @classmethod
    def from_path(cls, path: Path) -> "BaselineRecord":
        """Load a baseline JSON from disk."""
        data = json.loads(path.read_text())
        return cls(
            system=data["system"],
            config=data["config"],
            label=data.get("label", "<unlabeled>"),
            dataset=data["dataset"],
            tasks_ran=int(data["tasks_ran"]),
            metrics=data["metrics"],
            captured_at=data.get("captured_at"),
            git_sha=data.get("git_sha"),
        )

    @property
    def display_label(self) -> str:
        """Legend label disambiguating the baseline.

        Format: ``"<system> / <config> (<label>)"``. The ``label`` field
        is included so two baselines that share ``system`` + ``config``
        but report from different sweeps (e.g. fixture-5-needles vs
        real-100-needles) don't collide on the X-axis hue.
        """
        return f"{self.system} / {self.config} ({self.label})"

    @property
    def legend_suffix(self) -> str:
        """Compact provenance string ``[<git_sha[:7]>, n=<tasks>]``.

        Sized to fit comfortably in a matplotlib legend without clipping.
        ``label`` is intentionally NOT duplicated here — it's already in
        :attr:`display_label`.
        """
        parts: list[str] = []
        if self.git_sha:
            parts.append(self.git_sha[:7])
        parts.append(f"n={self.tasks_ran}")
        return f" [{', '.join(parts)}]"


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
    records = [
        item if isinstance(item, BaselineRecord)
        else BaselineRecord.from_path(item)
        for item in baselines
    ]
    if not records:
        raise ValueError("plot_baselines requires at least one baseline")

    metrics = tuple(metrics)
    if not metrics:
        raise ValueError("plot_baselines requires at least one metric")

    df = _long_dataframe(records, metrics)

    sns.set_theme(style="whitegrid", context="paper", font_scale=1.1)
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
        title = f"{records[0].dataset} ({records[0].tasks_ran} tasks)"
    ax.set_title(title)

    # Legend with compact provenance suffix per system.
    handles, labels = ax.get_legend_handles_labels()
    detailed = []
    for label in labels:
        rec = next(r for r in records if r.display_label == label)
        detailed.append(label + rec.legend_suffix)
    ax.legend(handles, detailed, title="System", loc="best", frameon=True)

    fig.tight_layout()

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=150, bbox_inches="tight")

    return fig


def _long_dataframe(
    records: list[BaselineRecord], metrics: tuple[str, ...],
) -> pd.DataFrame:
    """Reshape records × metrics into a long-form DataFrame for seaborn."""
    rows: list[dict[str, float | str]] = []
    for rec in records:
        for metric in metrics:
            if metric not in rec.metrics:
                continue
            stats = rec.metrics[metric]
            mean = float(stats.get("mean", 0.0))
            rows.append({
                "system": rec.display_label,
                "metric": metric,
                "value": mean,
                "ci_low": float(stats.get("ci_low", mean)),
                "ci_high": float(stats.get("ci_high", mean)),
            })
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
        sys_df = (
            df[df["system"] == system]
            .set_index("metric")
            .reindex(list(metric_order))
        )
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
            "Grouped bar plot of one or more baseline JSON files. "
            "Side-by-side bars per system; one X-axis tick per metric; "
            "95%% CI error bars; legend includes git_sha and tasks_ran."
        ),
    )
    parser.add_argument(
        "baselines",
        nargs="+",
        type=Path,
        help="One or more baseline JSON paths (e.g. benchmarks/baselines/repoqa_snf.json).",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        required=True,
        help="Output path for the figure (.png, .pdf, .svg).",
    )
    parser.add_argument(
        "--metrics",
        type=str,
        default=",".join(_DEFAULT_METRICS),
        help=f"Comma-separated metric names. Default: {','.join(_DEFAULT_METRICS)}",
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
    metrics = tuple(m.strip() for m in args.metrics.split(",") if m.strip())
    fig = plot_baselines(
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


__all__ = ("BaselineRecord", "plot_baselines")
