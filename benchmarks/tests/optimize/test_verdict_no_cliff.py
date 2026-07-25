"""A skipped judge must not erase the deterministic signal already paid for.

`judge_skipped ⇒ verdict = 0.0` was defensible when gates were pure screens: a
failed gate meant "do not score this". Once the deterministic layer carries a
graded score, the cliff throws away real measurement — a sample that satisfied
most of its checks scores identically to one that satisfied none, AFTER the
harness paid to compute both.

Opt-in via `keep_deterministic_on_skip`, so the default objective is unchanged
(the flag folds into `rubric_config_hash`, since it changes what is measured).
"""

from __future__ import annotations

import pytest

from pydocs_eval.optimize.rubric.model import (
    GateCheck,
    RubricConfig,
    RubricCriterion,
    rubric_config_hash,
)


def _config(**kw) -> RubricConfig:
    return RubricConfig(
        gates=(GateCheck("g", "min_answer_chars", {"n": 40}),),
        criteria=(RubricCriterion("c", 1.0, "d"),),
        **kw,
    )


def test_flag_defaults_to_todays_cliff() -> None:
    assert _config().keep_deterministic_on_skip is False


def test_flag_changes_the_objective_identity() -> None:
    """It changes WHAT is measured, so it must key the ledgers (spec §3.6)."""
    a = rubric_config_hash(_config(), architecture="text_react")
    b = rubric_config_hash(_config(keep_deterministic_on_skip=True), architecture="text_react")
    assert a != b


@pytest.mark.parametrize(
    ("keep", "gate_fraction", "expected"),
    [
        (False, 0.75, 0.0),  # today: everything discarded
        (True, 0.75, 0.3 * 0.75),  # kept: the deterministic layer still counts
        (True, 0.0, 0.0),  # nothing satisfied -> still zero, as it should be
    ],
)
def test_skipped_judge_verdict(keep: bool, gate_fraction: float, expected: float) -> None:
    from pydocs_eval.optimize.fitness.ask_rubric import verdict_when_judge_skipped

    cfg = _config(keep_deterministic_on_skip=keep)
    assert verdict_when_judge_skipped(cfg, gate_fraction) == pytest.approx(expected)


def test_kept_verdict_never_exceeds_the_gate_layer_weight() -> None:
    """A skipped judge can never score as well as a judged one — the rubric
    weight is simply unearned, not redistributed."""
    from pydocs_eval.optimize.fitness.ask_rubric import verdict_when_judge_skipped

    cfg = _config(keep_deterministic_on_skip=True)
    assert verdict_when_judge_skipped(cfg, 1.0) == pytest.approx(cfg.gate_weight)
    assert verdict_when_judge_skipped(cfg, 1.0) < 1.0
