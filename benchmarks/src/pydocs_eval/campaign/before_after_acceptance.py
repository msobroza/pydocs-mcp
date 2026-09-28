"""The acceptance rule every paid step is decided by: PASS or FAIL, and which rule said so.

Two rules, both read off paired point estimates (``before_after_compare``):

- **Standard** (the program spec's Q10): the penalised turns-to-answer goes down
  (point estimate), budget exhaustion does not rise, and ``needle cited`` stays
  within the correctness band below the baseline's — with the same guard on
  gold-site coverage over the multi-site tasks of a multi-location slice.
- **Completeness** (Q44, the step-8 arm only, ``--completeness-arm``): gold-site
  coverage improves and the penalised mean rises by at most
  :data:`COMPLETENESS_TURN_ALLOWANCE`; the step is then adopted only with the
  owner's sign-off. When it does not hold, the standard rule decides.

Call counts are not a gate. ``needle cited`` reading ``n/a`` — arms that stored
no answers — means no verdict at all: the correctness guard is what an
acceptance rests on (ADR 0025).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

#: Q44: how far the penalised turns-to-answer may rise for better coverage.
COMPLETENESS_TURN_ALLOWANCE = 0.5

# A rate over n tasks and a band of one task (1/n) meet exactly at the band's
# edge, where float subtraction can land a hair on the wrong side.
_TOLERANCE = 1e-9

#: The standard rule, as the comparison prints it.
STANDARD_RULE_SUMMARY = (
    "standard (Q10): penalised turns-to-answer down, budget exhaustion not up, "
    "needle cited within its band (and gold-site coverage within its own on multi-site tasks)"
)
#: The bounded step-8 rule, as the comparison prints it.
COMPLETENESS_RULE_SUMMARY = (
    "bounded step-8 (Q44) first: gold-site coverage up and penalised turns up by at most "
    f"+{COMPLETENESS_TURN_ALLOWANCE}, adopted on the owner's sign-off"
)


class VariantVerdict(StrEnum):
    """What the rule says of one variant."""

    PASSED = "PASS"
    FAILED = "FAIL"
    NO_VERDICT = "no verdict"


class AcceptanceRule(StrEnum):
    """Which rule decided: the standard Q10 rule, or the bounded step-8 rule Q44."""

    STANDARD = "Q10"
    COMPLETENESS = "Q44"


@dataclass(frozen=True, slots=True)
class PointPair:
    """One number on the baseline and on the variant, over the tasks both defined."""

    baseline: float
    variant: float

    @property
    def delta(self) -> float:
        """Variant minus baseline."""
        return self.variant - self.baseline


@dataclass(frozen=True, slots=True)
class CorrectnessBand:
    """How far each correctness guard may fall: the larger of the A/A difference and one task.

    ``None`` where the A/A pair shares no task that defines the number: an arm of
    the pair stored no answers, or, for coverage, the slice has no multi-site task.
    """

    needle_cited: float | None
    gold_site_coverage: float | None


@dataclass(frozen=True, slots=True)
class VariantEstimates:
    """What the rules read of one variant against the baseline; ``None`` where undefined."""

    penalised_turns: PointPair | None
    budget_exhausted: PointPair | None
    needle_cited: PointPair | None
    gold_site_coverage: PointPair | None


@dataclass(frozen=True, slots=True)
class VariantDecision:
    """A verdict, the rule that reached it, and every reason the rule read."""

    verdict: VariantVerdict
    rule: AcceptanceRule
    reasons: tuple[str, ...]

    @property
    def owner_sign_off(self) -> bool:
        """True when the bounded step-8 rule decided: Q44 adopts only on the owner's sign-off."""
        return self.rule is AcceptanceRule.COMPLETENESS


@dataclass(frozen=True, slots=True)
class _Check:
    """One guard of a rule: whether it held, and what it read."""

    held: bool
    reason: str


def decide_variant(
    estimates: VariantEstimates, band: CorrectnessBand, *, completeness_arm: bool
) -> VariantDecision:
    """PASS or FAIL for one variant; under ``completeness_arm`` the bounded rule is tried first.

    Example:
        >>> band = CorrectnessBand(needle_cited=0.05, gold_site_coverage=None)
        >>> faster = VariantEstimates(
        ...     PointPair(6.0, 5.0), PointPair(0.1, 0.1), PointPair(0.8, 0.8), None
        ... )
        >>> decide_variant(faster, band, completeness_arm=False).verdict
        <VariantVerdict.PASSED: 'PASS'>
    """
    if not completeness_arm:
        return _standard_decision(estimates, band)
    bounded = _completeness_check(estimates)
    if bounded.held:
        return VariantDecision(
            VariantVerdict.PASSED, AcceptanceRule.COMPLETENESS, (bounded.reason,)
        )
    standard = _standard_decision(estimates, band)
    return replace(standard, reasons=(bounded.reason, *standard.reasons))


def _standard_decision(estimates: VariantEstimates, band: CorrectnessBand) -> VariantDecision:
    """Q10: every guard must hold; an undefined one leaves no verdict to give."""
    turns, exhausted, cited = (
        estimates.penalised_turns,
        estimates.budget_exhausted,
        estimates.needle_cited,
    )
    if turns is None or exhausted is None or cited is None or band.needle_cited is None:
        return _no_verdict(estimates, band)
    checks = [
        _turns_went_down(turns),
        _exhaustion_did_not_rise(exhausted),
        _within_band("needle cited", cited, band.needle_cited),
        *_coverage_guard(estimates.gold_site_coverage, band.gold_site_coverage),
    ]
    verdict = (
        VariantVerdict.PASSED if all(check.held for check in checks) else VariantVerdict.FAILED
    )
    return VariantDecision(
        verdict, AcceptanceRule.STANDARD, tuple(check.reason for check in checks)
    )


def _no_verdict(estimates: VariantEstimates, band: CorrectnessBand) -> VariantDecision:
    """Name every number the standard rule needed and could not read."""
    needed = (
        ("penalised turns-to-answer", estimates.penalised_turns),
        ("budget-exhausted rate", estimates.budget_exhausted),
        ("needle cited", estimates.needle_cited),
        ("needle-cited band", band.needle_cited),
    )
    undefined = ", ".join(name for name, value in needed if value is None)
    reason = (
        f"{undefined} could not be read: arms without stored answers or outcomes get no verdict"
    )
    return VariantDecision(VariantVerdict.NO_VERDICT, AcceptanceRule.STANDARD, (reason,))


def _turns_went_down(turns: PointPair) -> _Check:
    if turns.delta < 0:
        return _Check(True, f"penalised turns-to-answer went down ({turns.delta:+.3f})")
    return _Check(False, f"penalised turns-to-answer did not go down ({turns.delta:+.3f})")


def _exhaustion_did_not_rise(exhausted: PointPair) -> _Check:
    if exhausted.delta <= _TOLERANCE:
        return _Check(True, f"budget exhaustion did not rise ({exhausted.delta:+.3f})")
    return _Check(False, f"budget exhaustion rose ({exhausted.delta:+.3f})")


def _within_band(name: str, pair: PointPair, band: float) -> _Check:
    """The guard: the variant stays at or above the baseline less the band."""
    shown = f"{pair.baseline:.3f} → {pair.variant:.3f}, band {band:.3f}"
    if pair.variant >= pair.baseline - band - _TOLERANCE:
        return _Check(True, f"{name} within the band ({shown})")
    return _Check(False, f"{name} fell past the band ({shown})")


def _coverage_guard(coverage: PointPair | None, band: float | None) -> list[_Check]:
    """The coverage twin, on a slice whose multi-site tasks both arms defined."""
    if coverage is None or band is None:
        return []
    return [_within_band("gold-site coverage", coverage, band)]


def _completeness_check(estimates: VariantEstimates) -> _Check:
    """Q44: coverage up, and the penalised mean up by at most the allowance."""
    coverage, turns = estimates.gold_site_coverage, estimates.penalised_turns
    if coverage is None or turns is None:
        return _Check(
            False,
            "bounded step-8 rule needs gold-site coverage and penalised turns, undefined here",
        )
    shown = (
        f"gold-site coverage {coverage.baseline:.3f} → {coverage.variant:.3f}, "
        f"penalised turns {turns.delta:+.3f} (allowance +{COMPLETENESS_TURN_ALLOWANCE})"
    )
    if coverage.delta > 0 and turns.delta <= COMPLETENESS_TURN_ALLOWANCE + _TOLERANCE:
        return _Check(True, f"bounded step-8 rule held: {shown}")
    return _Check(False, f"bounded step-8 rule did not hold: {shown}")


__all__ = (
    "COMPLETENESS_RULE_SUMMARY",
    "COMPLETENESS_TURN_ALLOWANCE",
    "STANDARD_RULE_SUMMARY",
    "AcceptanceRule",
    "CorrectnessBand",
    "PointPair",
    "VariantDecision",
    "VariantEstimates",
    "VariantVerdict",
    "decide_variant",
)
