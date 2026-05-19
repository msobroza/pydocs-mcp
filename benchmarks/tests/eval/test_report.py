"""Pin ``report.format_report``: markdown table with one column per
(system, config) pair and one row per metric (in the declared metric
order), each cell rendered as ``mean [ci_low, ci_high]`` percentages.

The reporter is the human-facing summary that lands as an MLflow artifact
+ a stdout block at the end of every sweep. Tests pin the table shape so
a downstream regression-diff script (Task 8) can parse it reliably.
"""
from __future__ import annotations

from benchmarks.eval.report import format_report


def _sample_results() -> dict[tuple[str, str], dict[str, tuple[float, float, float]]]:
    return {
        ("pydocs-mcp", "baseline"): {
            "recall@1": (0.60, 0.40, 0.80),
            "recall@5": (1.00, 1.00, 1.00),
            "recall@10": (1.00, 1.00, 1.00),
            "mrr": (0.75, 0.60, 0.90),
            "pass@1-needle": (0.60, 0.40, 0.80),
        },
        ("pydocs-mcp", "no_stdlib"): {
            "recall@1": (0.40, 0.20, 0.60),
            "recall@5": (0.80, 0.60, 1.00),
            "recall@10": (1.00, 1.00, 1.00),
            "mrr": (0.55, 0.40, 0.70),
            "pass@1-needle": (0.40, 0.20, 0.60),
        },
    }


def test_format_report_renders_one_column_per_system_config() -> None:
    report = format_report(
        sweep_results=_sample_results(),
        dataset_name="repoqa-fixture",
        n_tasks=5,
    )
    # WHY: assert the header row spells out both (system, config) pairs so a
    # future re-ordering of the dict doesn't silently drop one column.
    assert "pydocs-mcp / baseline" in report
    assert "pydocs-mcp / no_stdlib" in report
    # WHY: title line carries dataset name + task count — readers grep this
    # to verify the report matches the run they expect.
    assert "repoqa-fixture" in report
    assert "5 tasks" in report


def test_format_report_includes_metric_rows_in_order() -> None:
    report = format_report(
        sweep_results=_sample_results(),
        dataset_name="repoqa-fixture",
        n_tasks=5,
    )
    # WHY: metric order matters for downstream regression-diff scripts —
    # recall@1 first, pass@1-needle last. Walk the report lines and assert
    # the metric names appear in declared order.
    expected_order = ("recall@1", "recall@5", "recall@10", "mrr", "pass@1-needle")
    positions = [report.find(m) for m in expected_order]
    assert all(p >= 0 for p in positions), f"missing metric in report: {positions}"
    assert positions == sorted(positions), f"metrics out of order: {positions}"


def test_format_report_formats_cell_as_percent_with_ci() -> None:
    report = format_report(
        sweep_results=_sample_results(),
        dataset_name="repoqa-fixture",
        n_tasks=5,
    )
    # WHY: cell format is ``mean [ci_low, ci_high]`` with percentages. Pin
    # one known cell so the renderer can't silently drop CIs or switch
    # decimal places.
    assert "60.0% [40.0%, 80.0%]" in report
    assert "100.0% [100.0%, 100.0%]" in report


def test_format_report_renders_markdown_table_pipes() -> None:
    # WHY: markdown table syntax — header + separator + body. A reporter
    # that emits HTML or plaintext would break the artifact pipeline.
    report = format_report(
        sweep_results=_sample_results(),
        dataset_name="repoqa-fixture",
        n_tasks=5,
    )
    assert "|" in report
    # Markdown alignment row uses dashes between pipes.
    assert "---" in report


def test_format_report_handles_single_column() -> None:
    # WHY: a sweep over one (system, config) is the most common shape (one
    # checkpoint, baseline config). Empty CI rendering edge case lives in
    # this single-column test so the regression is obvious.
    single = {
        ("pydocs-mcp", "baseline"): {
            "recall@1": (0.5, 0.3, 0.7),
            "recall@5": (1.0, 1.0, 1.0),
            "recall@10": (1.0, 1.0, 1.0),
            "mrr": (0.75, 0.5, 1.0),
            "pass@1-needle": (0.5, 0.3, 0.7),
        },
    }
    report = format_report(
        sweep_results=single, dataset_name="repoqa", n_tasks=4,
    )
    assert "pydocs-mcp / baseline" in report
    assert "50.0% [30.0%, 70.0%]" in report


def test_format_report_metric_column_present() -> None:
    report = format_report(
        sweep_results=_sample_results(),
        dataset_name="repoqa-fixture",
        n_tasks=5,
    )
    # WHY: the leftmost column is the metric label. Pin its header so a
    # change in column ordering surfaces here, not in a flakier downstream
    # diff test.
    assert "Metric" in report
