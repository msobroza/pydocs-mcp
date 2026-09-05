"""Shared prelude for the per-figure plot modules.

One private home for the defaults and the load/validate/theme sequence the
three figure modules (``plot_baselines`` / ``plot_timings`` /
``plot_metric_vs_latency``) would otherwise each repeat. Title placement and
layout deliberately STAY in the figure modules — ``plot_timings`` uses
``fig.suptitle`` + constrained_layout while the others use ``ax.set_title``
+ ``tight_layout``, so folding them here would change visual output.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import seaborn as sns
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
    a fourth plot type must not copy it a fourth time. NOTE: the
    same-dataset guard runs before the empty-metrics check in
    plot_baselines/plot_timings (error-precedence swap only; both inputs
    still raise ValueError).
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
