"""The acceptance rule: PASS or FAIL for one variant against the baseline, and which rule decided.

Standard (Q10): penalised turns-to-answer down, budget exhaustion not up, and
``needle cited`` within the correctness band below the baseline (plus the same
guard on gold-site coverage over multi-site tasks). Completeness (Q44, step 8):
gold-site coverage up and the penalised mean up by at most +0.5, with the
owner's sign-off; when it does not pass, the standard rule decides.
"""

from __future__ import annotations

from pydocs_eval.campaign.before_after_acceptance import (
    AcceptanceRule,
    CorrectnessBand,
    PointPair,
    VariantEstimates,
    VariantVerdict,
    decide_variant,
)

_BAND = CorrectnessBand(needle_cited=0.05, gold_site_coverage=None)


def _estimates(
    *,
    turns: tuple[float, float] = (6.0, 5.0),
    exhausted: tuple[float, float] = (0.1, 0.1),
    cited: tuple[float, float] | None = (0.8, 0.8),
    coverage: tuple[float, float] | None = None,
) -> VariantEstimates:
    return VariantEstimates(
        penalised_turns=PointPair(*turns),
        budget_exhausted=PointPair(*exhausted),
        needle_cited=None if cited is None else PointPair(*cited),
        gold_site_coverage=None if coverage is None else PointPair(*coverage),
    )


def test_fewer_turns_no_more_exhaustion_and_correctness_held_passes() -> None:
    decision = decide_variant(_estimates(cited=(0.8, 0.76)), _BAND, completeness_arm=False)

    assert (decision.verdict, decision.rule) == (VariantVerdict.PASSED, AcceptanceRule.STANDARD)
    assert not decision.owner_sign_off


def test_budget_exhaustion_rising_fails() -> None:
    decision = decide_variant(_estimates(exhausted=(0.1, 0.2)), _BAND, completeness_arm=False)

    assert decision.verdict is VariantVerdict.FAILED
    assert any("budget exhaustion rose" in reason for reason in decision.reasons)


def test_needle_cited_falling_past_the_band_fails() -> None:
    decision = decide_variant(_estimates(cited=(0.8, 0.74)), _BAND, completeness_arm=False)

    assert decision.verdict is VariantVerdict.FAILED
    assert any("needle cited fell past the band" in reason for reason in decision.reasons)


def test_turns_that_did_not_go_down_fail() -> None:
    decision = decide_variant(_estimates(turns=(6.0, 6.0)), _BAND, completeness_arm=False)

    assert decision.verdict is VariantVerdict.FAILED


def test_coverage_falling_past_its_own_band_fails_on_a_multi_location_slice() -> None:
    band = CorrectnessBand(needle_cited=0.05, gold_site_coverage=0.1)

    decision = decide_variant(_estimates(coverage=(0.7, 0.55)), band, completeness_arm=False)

    assert decision.verdict is VariantVerdict.FAILED
    assert any("gold-site coverage fell past the band" in reason for reason in decision.reasons)


def test_without_stored_answers_there_is_no_verdict() -> None:
    decision = decide_variant(_estimates(cited=None), _BAND, completeness_arm=False)

    assert decision.verdict is VariantVerdict.NO_VERDICT
    assert any("needle cited" in reason for reason in decision.reasons)


def test_the_bounded_rule_passes_a_small_rise_with_better_coverage_on_sign_off() -> None:
    estimates = _estimates(turns=(6.0, 6.4), coverage=(0.5, 0.7))

    decision = decide_variant(estimates, _BAND, completeness_arm=True)

    assert (decision.verdict, decision.rule) == (VariantVerdict.PASSED, AcceptanceRule.COMPLETENESS)
    assert decision.owner_sign_off


def test_the_bounded_rule_fails_a_rise_past_half_a_turn_and_the_standard_rule_decides() -> None:
    estimates = _estimates(turns=(6.0, 6.6), coverage=(0.5, 0.7))

    decision = decide_variant(estimates, _BAND, completeness_arm=True)

    assert (decision.verdict, decision.rule) == (VariantVerdict.FAILED, AcceptanceRule.STANDARD)
    assert not decision.owner_sign_off


def test_without_the_flag_a_small_rise_with_better_coverage_still_fails() -> None:
    estimates = _estimates(turns=(6.0, 6.4), coverage=(0.5, 0.7))

    decision = decide_variant(estimates, _BAND, completeness_arm=False)

    assert (decision.verdict, decision.rule) == (VariantVerdict.FAILED, AcceptanceRule.STANDARD)
