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
from .runner import DEFAULT_METRIC_SPECS as _METRIC_ROW_ORDER


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
    for metric_name in _METRIC_ROW_ORDER:
        row = [metric_name]
        for key in columns:
            triple = sweep_results.get(key, {}).get(metric_name)
            row.append(_format_cell(triple))
        rows.append(row)

    table = "\n".join(_format_row(r) for r in rows)
    title = f"# Benchmark report — {dataset_name} ({n_tasks} tasks)"
    return f"{title}\n\n{table}\n"


def _format_cell(triple: tuple[float, float, float] | None) -> str:
    if triple is None:
        # WHY: missing metric for a column is rendered as ``—`` rather than
        # an empty cell — keeps the column count fixed so markdown
        # alignment doesn't drift on partial sweeps.
        return "—"
    mean, lo, hi = triple
    return f"{mean:.1%} [{lo:.1%}, {hi:.1%}]"


def _format_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"
