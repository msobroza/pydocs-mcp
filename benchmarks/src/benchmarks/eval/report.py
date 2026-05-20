"""Markdown report renderer (spec §4.11).

Produces a single GitHub-flavored markdown table with one column per
(system, config) pair and one row per metric. The runner writes the
result to ``report.md`` and logs it as an artifact on every tracker, so
the same text appears in MLflow + on stdout + (eventually) as a PR
comment.

Single function, no class — there is no plug-in axis to swap. If a
second report format (HTML, JSON) is ever needed, add a sibling function
rather than retrofitting a Protocol.
"""
from __future__ import annotations

# WHY: metric row order is part of the report contract — downstream
# regression-diff scripts walk the rows top-to-bottom and key on this
# sequence. Derive from ``runner.DEFAULT_METRIC_SPECS`` so the CLI
# ``--metrics`` default and the report's row order share a single source
# of truth — adding/removing a metric is a single-edit change in
# ``runner.py``. Don't reorder without updating the diff tool too.
# Latency rows (``LATENCY_KEYS``) render AFTER the quality metrics so a
# reader scanning top-down sees quality first (the headline number) and
# infrastructure cost second.
from .runner import DEFAULT_METRIC_SPECS as _METRIC_ROW_ORDER
from .runner import LATENCY_KEYS as _LATENCY_ROW_ORDER


def format_report(
    *,
    sweep_results: dict[tuple[str, str], dict[str, tuple[float, float, float]]],
    dataset_name: str,
    n_tasks: int,
) -> str:
    """Render a markdown table from a sweep's aggregated results.

    Args:
        sweep_results: ``{(system, config_name): {metric: (mean, lo, hi)}}``
            as returned by ``runner.run_sweep``.
        dataset_name: Human-readable dataset label for the title.
        n_tasks: Number of tasks aggregated, for the title line.

    Returns:
        A markdown string. The runner writes it to ``report.md`` and logs
        it via ``tracker.log_artifact``.
    """
    # WHY: iterate keys in insertion order (Python 3.7+ dict semantics) so
    # the column ordering follows the runner's sweep order. Callers can
    # rely on systems[0] × configs[0] landing in the first column.
    columns: list[tuple[str, str]] = list(sweep_results.keys())

    header_cells = ["Metric", *[f"{system} / {cfg}" for system, cfg in columns]]
    sep_cells = ["---"] * len(header_cells)

    rows: list[list[str]] = [header_cells, sep_cells]
    # WHY: quality metrics first (headline numbers), latency metrics below
    # (infrastructure cost). The two row groups share the same column
    # layout but use different cell renderers — _format_cell picks the
    # renderer by metric-name suffix.
    for metric_name in (*_METRIC_ROW_ORDER, *_LATENCY_ROW_ORDER):
        row = [metric_name]
        for key in columns:
            triple = sweep_results.get(key, {}).get(metric_name)
            row.append(_format_cell(metric_name, triple))
        rows.append(row)

    table = "\n".join(_format_row(r) for r in rows)
    title = f"# Benchmark report — {dataset_name} ({n_tasks} tasks)"
    return f"{title}\n\n{table}\n"


def _is_latency_metric(name: str) -> bool:
    """Routing predicate for the cell renderer (spec §5.5).

    Latency observations carry the ``_seconds`` suffix; quality metrics
    don't. This is the single disambiguator between the two triple
    shapes — ``(mean, ci_low, ci_high)`` vs ``(p50, p95, p99)`` — both
    of which share the same 3-tuple representation.
    """
    return name.endswith("_seconds")


def _format_cell(
    metric_name: str, triple: tuple[float, float, float] | None,
) -> str:
    if triple is None:
        # WHY: missing metric for a column is rendered as ``—`` rather than
        # an empty cell — keeps the column count fixed so markdown
        # alignment doesn't drift on partial sweeps.
        return "—"
    if _is_latency_metric(metric_name):
        # WHY: latency rows render as p50/p95/p99 in seconds with 2dp —
        # sub-second resolution is the operating regime; finer precision
        # is misleading because the underlying clock noise is on this scale.
        p50, p95, p99 = triple
        return f"p50 {p50:.2f}s | p95 {p95:.2f}s | p99 {p99:.2f}s"
    mean, lo, hi = triple
    return f"{mean:.1%} [{lo:.1%}, {hi:.1%}]"


def _format_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"
