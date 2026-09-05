"""Failure-taxonomy tests (ADR 0012): the first-match decision tree, the two
versioned detectors, the T5 degenerate synthetics, and the marker-based
``patch_apply_failed`` vs ``infra_error`` split.

The T5 run over the committed synthetics is the Task 7 fixture exercise: each
``meta.json``'s declared ``expected_taxonomy`` MUST be reproduced by the rules.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_eval.trajectory.attribution import load_events
from pydocs_eval.trajectory.eval_report import (
    GroundTruthOutcome,
    classify_infra_marker,
    infra_outcome,
    no_report_outcome,
    outcome_from_report,
    patch_apply_failed_outcome,
)
from pydocs_eval.trajectory.schema import LoopEvent, ToolEvent
from pydocs_eval.trajectory.taxonomy import (
    BUDGET_EXHAUSTED,
    CRASH_BEFORE_FIRST_TOOL,
    EMPTY_TRAJECTORY,
    FOUND_BUT_MISDIAGNOSED,
    INFRA_ERROR,
    LOCALIZATION_MISS,
    NEVER_RAN_TESTS,
    PATCH_APPLY_FAILED,
    REGRESSION_INTRODUCED,
    RESOLVED,
    RIGHT_IDEA_BROKEN_EDIT,
    TaxonomyInputs,
    budget_exhausted,
    classify,
    load_taxonomy_config,
    ran_tests,
)

_SYNTH = Path(__file__).parent / "fixtures" / "trajectories" / "synthetic"
_ATTR = Path(__file__).parent / "fixtures" / "trajectories" / "attribution"


def _events(case_dir: Path) -> tuple:
    return load_events(case_dir / "events.jsonl")


def _outcome_for(case_dir: Path, meta: dict) -> GroundTruthOutcome:
    """Build the ground-truth outcome the way the parser would for this case."""
    instance_id = meta["instance_id"]
    log = case_dir / "run_log.txt"
    if log.exists():
        marker = classify_infra_marker(log.read_text(encoding="utf-8"))
        if marker == "patch_apply_failed":
            return patch_apply_failed_outcome(instance_id)
        if marker == "infra_error":
            return infra_outcome(instance_id)
    return no_report_outcome(instance_id)


def _inputs_for(case_dir: Path, meta: dict) -> TaxonomyInputs:
    events = _events(case_dir)
    tools = tuple(e for e in events if isinstance(e, ToolEvent))
    loops = tuple(e for e in events if isinstance(e, LoopEvent))
    return TaxonomyInputs(
        outcome=_outcome_for(case_dir, meta),
        tool_events=tools,
        loop_events=loops,
        patch_bytes=meta.get("patch_bytes", 0),
        gold_surfaced=False,
        patch_touches_gold=False,
        f2p_fraction=0.0,
        p2p_regressions=0,
        num_turns=len({e.turn for e in events}),
        total_tokens=0,
        wall_seconds=0.0,
        turn_cap=15,
    )


@pytest.mark.parametrize(
    "case",
    ["empty_trajectory", "crash_before_first_tool", "patch_apply_failed", "infra_error"],
)
def test_t5_synthetic_declared_label_reproduced(case: str) -> None:
    """Each T5 synthetic's meta.json ``expected_taxonomy`` is reproduced (Task 7)."""
    case_dir = _SYNTH / case
    meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))
    label = classify(_inputs_for(case_dir, meta))
    assert label.label == meta["expected_taxonomy"]
    assert label.taxonomy_version == load_taxonomy_config().version


def test_marker_split_patch_apply_failed_vs_infra_error() -> None:
    """The apply-failure marker → model fault; the tests-errored marker → infra."""
    apply_log = (_SYNTH / "patch_apply_failed" / "run_log.txt").read_text(encoding="utf-8")
    infra_log = (_SYNTH / "infra_error" / "run_log.txt").read_text(encoding="utf-8")
    assert classify_infra_marker(apply_log) == "patch_apply_failed"
    assert classify_infra_marker(infra_log) == "infra_error"


def test_infra_error_excluded_from_aggregates_but_others_not() -> None:
    """Only ``infra_error`` is flagged excluded_from_aggregates (ADR 0012)."""
    infra = _inputs_for(
        _SYNTH / "infra_error",
        json.loads((_SYNTH / "infra_error" / "meta.json").read_text()),
    )
    apply = _inputs_for(
        _SYNTH / "patch_apply_failed",
        json.loads((_SYNTH / "patch_apply_failed" / "meta.json").read_text()),
    )
    assert classify(infra).excluded_from_aggregates is True
    assert classify(apply).excluded_from_aggregates is False


# --- Versioned test-runner detector (ran over a T6 merged stream) -----------


def _bash(command: str) -> LoopEvent:
    return LoopEvent(
        event_id="b",
        trajectory_id="t",
        kind="tool_use",
        turn=2,
        tool="Bash",
        tool_input={"command": command},
    )


@pytest.mark.parametrize(
    "command",
    ["pytest -q", "python -m pytest tests/", "python -m unittest", "tox", "unittest discover"],
)
def test_ran_tests_matches_versioned_pattern_set(command: str) -> None:
    """Every shipped test-runner pattern is detected on a synthetic Bash event."""
    assert ran_tests((_bash(command),), load_taxonomy_config()) is True


@pytest.mark.parametrize("command", ["ls -la", "git status", "cat pytest_helper.py"])
def test_ran_tests_rejects_non_test_commands(command: str) -> None:
    """Non-test Bash commands (incl. a path containing 'pytest') never match."""
    assert ran_tests((_bash(command),), load_taxonomy_config()) is False


def test_detector_over_t6_merged_stream_flips_never_ran_tests() -> None:
    """On a real T6 merged stream, adding a Bash test run flips never_ran_tests.

    Uses the ``search_surfaces_gold`` T6 fixture (gold surfaced + edited): without
    a test run it is ``never_ran_tests``; appending a synthetic ``pytest`` Bash
    event makes the detector fire and the label advances past never_ran_tests.
    """
    events = load_events(_ATTR / "search_surfaces_gold" / "events.jsonl")
    tools = tuple(e for e in events if isinstance(e, ToolEvent))
    loops = tuple(e for e in events if isinstance(e, LoopEvent))
    base = _t6_inputs(tools, loops)
    assert classify(base).label == NEVER_RAN_TESTS
    with_tests = _t6_inputs(tools, loops + (_bash("pytest -q"),))
    assert classify(with_tests).label != NEVER_RAN_TESTS


def _t6_inputs(
    tools: tuple, loops: tuple, *, f2p: float = 1.0, p2p: int = 0, resolved: bool = False
) -> TaxonomyInputs:
    """A merged trajectory over gold that was surfaced + edited.

    ``resolved`` builds a consistent ground-truth outcome (the parser re-derives
    ``resolved`` from the test lists, so the flag must shape the lists);
    ``f2p`` / ``p2p`` drive the later failure branches independently.
    """
    return TaxonomyInputs(
        outcome=_graded_outcome(resolved=resolved),
        tool_events=tools,
        loop_events=loops,
        patch_bytes=256,
        gold_surfaced=True,
        patch_touches_gold=True,
        f2p_fraction=f2p,
        p2p_regressions=p2p,
        num_turns=len({e.turn for e in (*tools, *loops)}),
        total_tokens=0,
        wall_seconds=0.0,
        turn_cap=15,
    )


def _graded_outcome(*, resolved: bool) -> GroundTruthOutcome:
    """A graded outcome whose re-derived resolve equals ``resolved`` (test lists shaped)."""
    f2p = {"success": ["t::a"], "failure": []} if resolved else {"success": [], "failure": ["t::a"]}
    return outcome_from_report(
        "widgetlib__pricing-discount",
        {
            "widgetlib__pricing-discount": {
                "patch_successfully_applied": True,
                "resolved": resolved,
                "tests_status": {
                    "FAIL_TO_PASS": f2p,
                    "PASS_TO_PASS": {"success": ["t::b"], "failure": []},
                },
            }
        },
        gold_f2p=["t::a"],
        gold_p2p=["t::b"],
    )


# --- Budget predicate: inert null-cap clauses (ADR 0012) --------------------


def _budget_inputs(**kw) -> TaxonomyInputs:
    base = dict(
        outcome=no_report_outcome("i"),
        tool_events=(),
        loop_events=(),
        patch_bytes=0,
        gold_surfaced=False,
        patch_touches_gold=False,
        f2p_fraction=0.0,
        p2p_regressions=0,
        num_turns=15,
        total_tokens=10**9,
        wall_seconds=10**9,
        turn_cap=15,
        token_cap=None,
        wall_cap=None,
    )
    base.update(kw)
    return TaxonomyInputs(**base)


def test_budget_exhausted_fires_only_on_recorded_turn_cap() -> None:
    """Turn cap hit + no patch → budget_exhausted; token/wall caps are inert (null)."""
    assert budget_exhausted(_budget_inputs()) is True


def test_budget_null_caps_never_fire_even_at_huge_usage() -> None:
    """All caps null → inert by construction; huge token/wall usage never fires."""
    hit = _budget_inputs(turn_cap=None)
    assert budget_exhausted(hit) is False


def test_budget_not_hit_when_turns_below_cap() -> None:
    assert budget_exhausted(_budget_inputs(num_turns=5)) is False


def test_budget_requires_no_patch() -> None:
    """A produced patch means the cap did not truncate the work — not budget-exhausted."""
    assert budget_exhausted(_budget_inputs(patch_bytes=512)) is False


# --- Failure-branch ordering + resolved terminal ----------------------------


def test_resolved_short_circuits_before_never_ran_tests() -> None:
    """A resolved run that skipped self-testing is ``resolved``, not never_ran_tests."""
    inputs = _t6_inputs((), (), resolved=True)
    assert classify(inputs).label == RESOLVED


def test_localization_miss_when_no_gold_surfaced() -> None:
    inputs = replace(_t6_inputs((), (_bash("pytest"),)), gold_surfaced=False)
    assert classify(inputs).label == LOCALIZATION_MISS


def test_found_but_misdiagnosed_when_patch_misses_gold() -> None:
    inputs = replace(
        _t6_inputs((), (_bash("pytest"),)), gold_surfaced=True, patch_touches_gold=False
    )
    assert classify(inputs).label == FOUND_BUT_MISDIAGNOSED


def test_right_idea_broken_edit_when_f2p_incomplete() -> None:
    inputs = _t6_inputs((), (_bash("pytest"),), f2p=0.5)
    assert classify(inputs).label == RIGHT_IDEA_BROKEN_EDIT


def test_regression_introduced_when_p2p_regresses() -> None:
    inputs = _t6_inputs((), (_bash("pytest"),), f2p=1.0, p2p=1)
    assert classify(inputs).label == REGRESSION_INTRODUCED
