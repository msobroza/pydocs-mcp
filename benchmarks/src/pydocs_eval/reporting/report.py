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

from collections.abc import Mapping, Sequence

# WHY: metric row order is part of the report contract — downstream
# regression-diff scripts walk the rows top-to-bottom and key on this
# sequence. Derive from ``sweep.DEFAULT_METRIC_SPECS`` — the constants'
# home module (``runner`` only re-exports them for compatibility) — so the
# CLI ``--metrics`` default and the report's row order share a single
# source of truth: adding/removing a metric is a single-edit change in
# ``sweep.py``. Don't reorder without updating the diff tool too.
# Latency rows (``LATENCY_KEYS``) render AFTER the quality metrics so a
# reader scanning top-down sees quality first (the headline number) and
# infrastructure cost second.
from ..sweep import DEFAULT_METRIC_SPECS as _METRIC_ROW_ORDER
from ..sweep import LATENCY_KEYS as _LATENCY_ROW_ORDER

# WHY: SWE-QA-Pro tags each task with a category (What/Where/How/Why) under
# ``metadata["qa_type"]``. A category breakout only carries signal when the
# dataset actually mixes categories — RepoQA, DS-1000, and any other dataset
# without the key must render exactly as before. Require ≥2 distinct values
# so a single-category run (or a keyless one) suppresses the section entirely.
_CATEGORY_KEY = "qa_type"
_MIN_BREAKOUT_CATEGORIES = 2

# Per-task detail the breakout needs: one row per task, keyed like
# ``sweep_results`` so the breakout sub-table shares the main table's columns.
# ``{(system, config): ({"metadata": {...}, "scores": {metric: value}}, …)}``.
TaskRow = Mapping[str, object]
TaskRows = Mapping[tuple[str, str], Sequence[TaskRow]]


def format_report(
    *,
    sweep_results: dict[tuple[str, str], dict[str, tuple[float, float, float]]],
    dataset_name: str,
    n_tasks: int,
    task_rows: TaskRows | None = None,
    metric_specs: tuple[str, ...] = _METRIC_ROW_ORDER,
) -> str:
    """Render a markdown table from a sweep's aggregated results.

    Args:
        sweep_results: ``{(system, config_name): {metric: (mean, lo, hi)}}``
            as returned by ``runner.run_sweep``.
        dataset_name: Human-readable dataset label for the title.
        n_tasks: Number of tasks aggregated, for the title line.
        task_rows: Optional per-task detail keyed like ``sweep_results``.
            When ≥2 distinct ``metadata["qa_type"]`` values are present the
            report grows a ``## By qa_type`` section with one row per category
            (per-category metric means). Absent or single-category → the top
            table renders byte-identical to a call without this argument.
        metric_specs: Quality-metric row order. Defaults to
            ``DEFAULT_METRIC_SPECS`` (the historic row order downstream
            regression-diff scripts key on). Callers whose sweep used a
            non-default ``--metrics`` (e.g. ``ndcg@10,precision@1``) MUST
            pass the actual specs here — otherwise those metrics have no row
            and the default rows all render as ``—`` (the values simply
            aren't in ``sweep_results`` under the default names).

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
    for metric_name in (*metric_specs, *_LATENCY_ROW_ORDER):
        row = [metric_name]
        for key in columns:
            triple = sweep_results.get(key, {}).get(metric_name)
            row.append(_format_cell(metric_name, triple))
        rows.append(row)

    table = "\n".join(_format_row(r) for r in rows)
    title = f"# Benchmark report — {dataset_name} ({n_tasks} tasks)"
    report = f"{title}\n\n{table}\n"

    # WHY: append the category breakout AFTER the top table so the headline
    # numbers stay first and the top table is untouched when the section is
    # suppressed (keeps the no-``task_rows`` render byte-identical).
    breakout = _format_category_breakout(task_rows)
    if breakout:
        report += f"\n{breakout}\n"
    return report


def _is_latency_metric(name: str) -> bool:
    """Routing predicate for the cell renderer (spec §5.5).

    Latency observations carry the ``_seconds`` suffix; quality metrics
    don't. This is the single disambiguator between the two triple
    shapes — ``(mean, ci_low, ci_high)`` vs ``(p50, p95, p99)`` — both
    of which share the same 3-tuple representation.
    """
    return name.endswith("_seconds")


def _format_cell(
    metric_name: str,
    triple: tuple[float, float, float] | None,
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


def _distinct_categories(task_rows: TaskRows) -> tuple[str, ...]:
    """Ordered-unique ``qa_type`` values across every leg's task rows.

    Insertion order (first-seen) so the breakout rows follow the order the
    categories appear in the dataset stream — deterministic across runs.
    """
    seen: dict[str, None] = {}
    for rows in task_rows.values():
        for row in rows:
            metadata = row.get("metadata")
            if not isinstance(metadata, Mapping):
                continue
            category = metadata.get(_CATEGORY_KEY)
            if isinstance(category, str):
                seen.setdefault(category, None)
    return tuple(seen)


def _category_mean(
    task_rows: TaskRows,
    category: str,
    metric_name: str,
) -> float | None:
    """Mean of ``metric_name`` over every task tagged ``category``.

    Pools across all (system, config) legs — the breakout answers "how does
    this category do overall", not per-leg. ``None`` when no tagged task
    scored the metric → rendered as ``—`` so the column count stays fixed on
    partial coverage.
    """
    values: list[float] = []
    for rows in task_rows.values():
        for row in rows:
            metadata = row.get("metadata")
            if not isinstance(metadata, Mapping) or metadata.get(_CATEGORY_KEY) != category:
                continue
            scores = row.get("scores")
            if not isinstance(scores, Mapping):
                continue
            value = scores.get(metric_name)
            if isinstance(value, (int, float)):
                values.append(float(value))
    if not values:
        return None
    return sum(values) / len(values)


def _format_category_breakout(task_rows: TaskRows | None) -> str:
    """A ``## By qa_type`` section: one row per category, one metric column each.

    Empty string when ``task_rows`` is absent or carries fewer than
    ``_MIN_BREAKOUT_CATEGORIES`` distinct ``qa_type`` values — the caller
    then leaves the top table untouched.
    """
    if not task_rows:
        return ""
    categories = _distinct_categories(task_rows)
    if len(categories) < _MIN_BREAKOUT_CATEGORIES:
        return ""

    # WHY: the breakout reuses the main table's quality-metric names as
    # columns so a reader compares category means against the overall row
    # metric-for-metric. Latency keys are excluded — per-category timing
    # percentiles aren't a per-category quality signal. Means pool ACROSS
    # (system, config) legs: the breakout answers "how does this category do
    # overall", not per-leg.
    header_cells = [_CATEGORY_KEY, *_METRIC_ROW_ORDER]
    sep_cells = ["---"] * len(header_cells)
    rows: list[list[str]] = [header_cells, sep_cells]

    for category in categories:
        cells = [category]
        for metric_name in _METRIC_ROW_ORDER:
            mean = _category_mean(task_rows, category, metric_name)
            cells.append("—" if mean is None else f"{mean:.1%}")
        rows.append(cells)

    table = "\n".join(_format_row(r) for r in rows)
    return f"## By {_CATEGORY_KEY}\n\n{table}"
