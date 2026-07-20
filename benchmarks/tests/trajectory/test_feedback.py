"""Feedback-template tests (ADR 0012): facts-only strings, the 2000-char default
bound, non-empty on every failure, error-carrying degenerate lines, and the
never-raises contract.
"""

from __future__ import annotations

from pydocs_eval.trajectory.attribution import Attribution
from pydocs_eval.trajectory.eval_report import GroundTruthOutcome
from pydocs_eval.trajectory.feedback import _DEFAULT_MAX_CHARS, build_feedback
from pydocs_eval.trajectory.metrics import HunkOverlapReport, TokenTotals, TrajectoryMetrics
from pydocs_eval.trajectory.taxonomy import EMPTY_TRAJECTORY, RIGHT_IDEA_BROKEN_EDIT


def _attribution(*, first_touch=None, wasted=frozenset()) -> Attribution:
    return Attribution(
        surfacings=(),
        surfaced_files=frozenset(),
        inspected_files=frozenset(),
        used_files=frozenset(),
        wasted_reads=wasted,
        first_touch=first_touch or {},
    )


def _metrics(*, turns: int = 3) -> TrajectoryMetrics:
    return TrajectoryMetrics(
        gold_file_recall=1.0,
        wasted_read_ratio=0.0,
        mean_hunk_overlap=1.0,
        hunk_overlap=HunkOverlapReport(by_file={}, file_level_only=frozenset()),
        tool_calls_to_first_gold=1,
        per_tool_yield={},
        patch_applies=True,
        f2p_fraction=0.0,
        p2p_regression_count=0,
        tokens=TokenTotals(5100, 220, 0, 0),
        calls_by_tool={},
        turns=turns,
        wall_clock_seconds=0.0,
        cost_usd=0.0,
        tool_calls=4,
    )


def _outcome(*, f2p_passed=frozenset()) -> GroundTruthOutcome:
    empty: frozenset[str] = frozenset()
    return GroundTruthOutcome(
        instance_id="i",
        resolved=False,
        patch_applied=True,
        infra_error=False,
        patch_apply_failed=False,
        f2p_passed=f2p_passed,
        f2p_failed=empty,
        p2p_passed=empty,
        p2p_failed=empty,
        upstream_resolved=None,
    )


def test_feedback_states_gold_first_surfacer_and_wasted_reads() -> None:
    text = build_feedback(
        label=RIGHT_IDEA_BROKEN_EDIT,
        metrics=_metrics(),
        attribution=_attribution(
            first_touch={"widgetlib/pricing.py": "search_codebase"},
            wasted=frozenset({"widgetlib/textutil.py"}),
        ),
        outcome=_outcome(),
        gold_files=frozenset({"widgetlib/pricing.py"}),
        gold_f2p=frozenset({"tests/test_pricing.py::test_discount"}),
        turn_cap=15,
    )
    assert "widgetlib/pricing.py (first surfaced by search_codebase)" in text
    assert "Wasted reads (inspected, never edited): widgetlib/textutil.py." in text
    assert "tests/test_pricing.py::test_discount" in text
    assert "turns 3/15" in text
    assert "tokens in/out 5100/220" in text


def test_gold_file_not_surfaced_is_reported() -> None:
    text = build_feedback(
        label=RIGHT_IDEA_BROKEN_EDIT,
        metrics=_metrics(),
        attribution=_attribution(),
        outcome=_outcome(),
        gold_files=frozenset({"widgetlib/pricing.py"}),
        gold_f2p=frozenset(),
        turn_cap=None,
    )
    assert "widgetlib/pricing.py (not surfaced)" in text
    assert "no cap recorded" in text


def test_degenerate_label_carries_error_line_and_is_non_empty() -> None:
    text = build_feedback(
        label=EMPTY_TRAJECTORY,
        metrics=_metrics(turns=0),
        attribution=_attribution(),
        outcome=_outcome(),
        gold_files=frozenset(),
        gold_f2p=frozenset(),
        turn_cap=15,
    )
    assert text
    assert "Empty trajectory" in text


def test_passing_target_tests_reported_as_none() -> None:
    text = build_feedback(
        label=RIGHT_IDEA_BROKEN_EDIT,
        metrics=_metrics(),
        attribution=_attribution(),
        outcome=_outcome(f2p_passed=frozenset({"t::a"})),
        gold_files=frozenset(),
        gold_f2p=frozenset({"t::a"}),
        turn_cap=15,
    )
    assert "Failing target tests: none." in text


def test_feedback_bounded_to_max_chars() -> None:
    """A pathologically large wasted-read set is truncated to the default bound."""
    wasted = frozenset(f"widgetlib/mod_{i:04d}.py" for i in range(1000))
    text = build_feedback(
        label=RIGHT_IDEA_BROKEN_EDIT,
        metrics=_metrics(),
        attribution=_attribution(wasted=wasted),
        outcome=_outcome(),
        gold_files=frozenset(),
        gold_f2p=frozenset(),
        turn_cap=15,
    )
    assert len(text) <= _DEFAULT_MAX_CHARS
    assert text  # non-empty even when truncated


def test_feedback_never_raises_on_broken_metrics() -> None:
    """A metrics-like object that raises on attribute access still yields the floor."""

    class Exploding:
        def __getattr__(self, name: str):
            raise RuntimeError("boom")

    text = build_feedback(
        label="localization_miss",
        metrics=Exploding(),  # type: ignore[arg-type]
        attribution=_attribution(),
        outcome=_outcome(),
        gold_files=frozenset(),
        gold_f2p=frozenset(),
        turn_cap=15,
    )
    assert text == "Trajectory outcome: localization_miss."
