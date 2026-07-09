"""Per-category (``qa_type``) report breakout (spec §D14).

SWE-QA-Pro tags each task with a ``qa_type`` class (What/Where/How/Why).
``format_report`` grows an optional ``task_rows`` argument carrying the
per-task metric scores + metadata; when ≥2 distinct ``qa_type`` values are
present it appends a ``## By qa_type`` section with one row per category
and the same metric columns as the main table. Datasets without the key
(RepoQA) render no such section and the top table stays byte-identical.
"""

from __future__ import annotations

from pydocs_eval.report import format_report


def _sample_results() -> dict[tuple[str, str], dict[str, tuple[float, float, float]]]:
    return {
        ("pydocs-mcp", "baseline"): {
            "recall@1": (0.60, 0.40, 0.80),
            "recall@5": (1.00, 1.00, 1.00),
            "recall@10": (1.00, 1.00, 1.00),
            "mrr": (0.75, 0.60, 0.90),
            "pass@1-needle": (0.60, 0.40, 0.80),
        },
    }


def _task_rows_with_qa_type() -> dict[tuple[str, str], tuple[dict[str, object], ...]]:
    # Two What tasks, one Where task: ≥2 distinct qa_type values → breakout.
    return {
        ("pydocs-mcp", "baseline"): (
            {
                "metadata": {"qa_type": "What"},
                "scores": {"recall@1": 1.0, "recall@5": 1.0, "mrr": 1.0},
            },
            {
                "metadata": {"qa_type": "What"},
                "scores": {"recall@1": 0.0, "recall@5": 1.0, "mrr": 0.5},
            },
            {
                "metadata": {"qa_type": "Where"},
                "scores": {"recall@1": 1.0, "recall@5": 1.0, "mrr": 1.0},
            },
        ),
    }


def test_breakout_section_present_with_two_categories() -> None:
    report = format_report(
        sweep_results=_sample_results(),
        dataset_name="swe-qa-pro-fixture",
        n_tasks=3,
        task_rows=_task_rows_with_qa_type(),
    )
    assert "## By qa_type" in report


def test_breakout_has_one_row_per_category() -> None:
    report = format_report(
        sweep_results=_sample_results(),
        dataset_name="swe-qa-pro-fixture",
        n_tasks=3,
        task_rows=_task_rows_with_qa_type(),
    )
    section = report.split("## By qa_type", 1)[1]
    # WHY: category label is the leftmost cell of each breakout row; both
    # distinct qa_type values must appear exactly once.
    assert section.count("| What |") == 1
    assert section.count("| Where |") == 1


def test_breakout_shares_metric_columns_with_main_table() -> None:
    report = format_report(
        sweep_results=_sample_results(),
        dataset_name="swe-qa-pro-fixture",
        n_tasks=3,
        task_rows=_task_rows_with_qa_type(),
    )
    section = report.split("## By qa_type", 1)[1]
    # WHY: the breakout uses the same metric columns as the main table so a
    # reader compares category means against the overall row-for-row.
    for metric in ("recall@1", "recall@5", "recall@10", "mrr", "pass@1-needle"):
        assert metric in section


def test_breakout_means_per_category() -> None:
    report = format_report(
        sweep_results=_sample_results(),
        dataset_name="swe-qa-pro-fixture",
        n_tasks=3,
        task_rows=_task_rows_with_qa_type(),
    )
    section = report.split("## By qa_type", 1)[1]
    # WHY: What has recall@1 {1.0, 0.0} → mean 50.0%; mrr {1.0, 0.5} → 75.0%.
    # Where has a single task recall@1 1.0 → 100.0%.
    assert "50.0%" in section
    assert "75.0%" in section
    assert "100.0%" in section


def test_no_qa_type_renders_no_breakout_and_leaves_top_table_identical() -> None:
    # WHY: RepoQA-shaped run — no task carries qa_type. The breakout must be
    # absent AND the top table byte-identical to the no-task_rows report.
    baseline = format_report(
        sweep_results=_sample_results(),
        dataset_name="repoqa-fixture",
        n_tasks=5,
    )
    rows_without_key = {
        ("pydocs-mcp", "baseline"): (
            {"metadata": {}, "scores": {"recall@1": 1.0}},
            {"metadata": {"library": "numpy"}, "scores": {"recall@1": 0.0}},
        ),
    }
    with_rows = format_report(
        sweep_results=_sample_results(),
        dataset_name="repoqa-fixture",
        n_tasks=5,
        task_rows=rows_without_key,
    )
    assert "## By qa_type" not in with_rows
    assert with_rows == baseline


def test_single_category_renders_no_breakout() -> None:
    # WHY: a breakout of one category adds no signal over the overall row —
    # require ≥2 distinct qa_type values before emitting the section.
    single_category = {
        ("pydocs-mcp", "baseline"): (
            {"metadata": {"qa_type": "What"}, "scores": {"recall@1": 1.0}},
            {"metadata": {"qa_type": "What"}, "scores": {"recall@1": 0.0}},
        ),
    }
    report = format_report(
        sweep_results=_sample_results(),
        dataset_name="swe-qa-pro-fixture",
        n_tasks=2,
        task_rows=single_category,
    )
    assert "## By qa_type" not in report


def test_top_table_byte_identical_when_task_rows_absent() -> None:
    # WHY: pin the existing top-table contract — adding the optional
    # ``task_rows`` argument must not perturb the default render path.
    from pydocs_eval.report import format_report as fr

    baseline = fr(
        sweep_results=_sample_results(),
        dataset_name="repoqa-fixture",
        n_tasks=5,
    )
    assert "## By qa_type" not in baseline
    assert baseline.startswith("# Benchmark report — repoqa-fixture (5 tasks)")
