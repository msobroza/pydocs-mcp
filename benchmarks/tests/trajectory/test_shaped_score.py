"""Shaped-score tests (ADR 0012): components are goodness-in-[0,1] facts, the
soft score is the weight-normalized average (always in [0,1]), and the
``score_version`` is stamped from the loaded weights config.
"""

from __future__ import annotations

import pytest

from pydocs_eval.trajectory.eval_report import GroundTruthOutcome, no_report_outcome
from pydocs_eval.trajectory.metrics import HunkOverlapReport, TokenTotals, TrajectoryMetrics
from pydocs_eval.trajectory.shaped_score import (
    ScoreWeights,
    compute_shaped_score,
    load_score_weights,
    score_components,
    shaped_soft_score,
)


def _metrics(
    *,
    recall: float = 1.0,
    wasted: float = 0.0,
    f2p: float = 1.0,
    p2p: int = 0,
    turns: int = 3,
) -> TrajectoryMetrics:
    return TrajectoryMetrics(
        gold_file_recall=recall,
        wasted_read_ratio=wasted,
        mean_hunk_overlap=1.0,
        hunk_overlap=HunkOverlapReport(by_file={}, file_level_only=frozenset()),
        tool_calls_to_first_gold=1,
        per_tool_yield={},
        patch_applies=True,
        f2p_fraction=f2p,
        p2p_regression_count=p2p,
        tokens=TokenTotals(0, 0, 0, 0),
        calls_by_tool={},
        turns=turns,
        wall_clock_seconds=0.0,
        cost_usd=0.0,
        tool_calls=1,
    )


def _outcome(*, applied: bool) -> GroundTruthOutcome:
    empty: frozenset[str] = frozenset()
    return GroundTruthOutcome(
        instance_id="i",
        resolved=False,
        patch_applied=applied,
        infra_error=False,
        patch_apply_failed=False,
        f2p_passed=empty,
        f2p_failed=empty,
        p2p_passed=empty,
        p2p_failed=empty,
        upstream_resolved=None,
    )


def test_perfect_trajectory_soft_is_one() -> None:
    """Every component maxed → weight-normalized average is exactly 1.0."""
    scored = compute_shaped_score(_metrics(), _outcome(applied=True), turn_cap=None)
    assert scored.soft == 1.0
    assert scored.score_version == load_score_weights().version


def test_worst_trajectory_soft_is_zero() -> None:
    """Every component zeroed → soft is exactly 0.0."""
    metrics = _metrics(recall=0.0, wasted=1.0, f2p=0.0, p2p=2, turns=15)
    scored = compute_shaped_score(metrics, _outcome(applied=False), turn_cap=15)
    assert scored.soft == 0.0


def test_soft_always_in_unit_interval() -> None:
    """Any component mix stays in [0,1] (the GEPA sum/mean calibration guarantee)."""
    metrics = _metrics(recall=0.5, wasted=0.5, f2p=0.5, p2p=1, turns=10)
    scored = compute_shaped_score(metrics, _outcome(applied=True), turn_cap=15)
    assert 0.0 <= scored.soft <= 1.0


def test_components_are_the_six_named_facts() -> None:
    comps = score_components(_metrics(wasted=0.25), _outcome(applied=True), turn_cap=None)
    assert set(comps) == {
        "localization_recall",
        "evidence_yield",
        "patch_applies",
        "f2p_fraction",
        "p2p_clean",
        "budget_headroom",
    }
    assert comps["evidence_yield"] == 0.75
    assert comps["patch_applies"] == 1.0


def test_budget_headroom_null_cap_is_full() -> None:
    """No recorded turn cap → full budget headroom (never penalized on a null cap)."""
    comps = score_components(_metrics(turns=999), _outcome(applied=True), turn_cap=None)
    assert comps["budget_headroom"] == 1.0


def test_all_zero_weights_raise_with_version() -> None:
    """A degenerate all-zero weights config raises, carrying the offending version."""
    zero = ScoreWeights(version=7, weights=dict.fromkeys(load_score_weights().weights, 0.0))
    with pytest.raises(ValueError, match="score_version 7"):
        shaped_soft_score(score_components(_metrics(), _outcome(applied=True), turn_cap=None), zero)
