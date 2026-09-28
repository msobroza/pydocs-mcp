"""Up to three variant arms against one baseline: Holm-adjusted p, the band, and a verdict.

The instrument every paid acceptance reads (#372). Each variant is paired with
the baseline by task id, over the tasks both defined (ADR 0016 §Statistics),
and gets:

- the one-sided Wilcoxon signed-rank p that it lowered the penalised
  turns-to-answer and the tool calls per task, and McNemar's exact two-sided p
  on Needle reached and ``needle cited`` — the report's own tests
  (``before_after_paired``) — each Holm-adjusted across the (at most
  :data:`MAX_VARIANTS`) variants, so no variant reads more significant than the
  family allows;
- the point estimates the acceptance rule reads, and its verdict
  (``before_after_acceptance``).

The correctness band comes from the A/A pair — the baseline and a second
baseline arm on the same commit and settings — as the larger of their
difference and one task, on ``needle cited`` and, over the multi-site tasks,
gold-site coverage (the program spec's "Acceptance"). The values read are the
report's own rows (``before_after_rows``), so a verdict reads what the report
prints.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import fmean
from types import MappingProxyType

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_acceptance import (
    CorrectnessBand,
    PointPair,
    VariantDecision,
    VariantEstimates,
    decide_variant,
)
from pydocs_eval.campaign.before_after_measure import ArmMetrics
from pydocs_eval.campaign.before_after_paired import (
    improvement_p,
    mcnemar_contrast,
    paired_values,
)
from pydocs_eval.campaign.before_after_rows import (
    MetricDirection,
    budget_exhausted_of_task,
    gold_reached_of_task,
    gold_site_coverage_of_task,
    needle_cited_of_task,
    penalised_turns_of_task,
    tool_calls_of_task,
)
from pydocs_eval.campaign.before_after_task_measurement import TaskValue
from pydocs_eval.metrics.aggregate import holm_adjust

#: Holm's family: the spec caps a comparison at three variants against one baseline.
MAX_VARIANTS = 3


class ComparisonInputError(MeasurementPlanError):
    """A ``before-after-compare`` input the operator must fix: the variants or an arm."""


class PairedTest(StrEnum):
    """The four paired tests a comparison prints, by their column label."""

    TURNS = "p turns"
    CALLS = "p calls"
    NEEDLE_REACHED = "p needle reached"
    NEEDLE_CITED = "p needle cited"


@dataclass(frozen=True, slots=True)
class LabelledArm:
    """One arm's measurements, under the directory it was read from."""

    label: str
    metrics: ArmMetrics


@dataclass(frozen=True, slots=True)
class VariantComparison:
    """One variant against the baseline: its pairs, estimates, adjusted p and verdict."""

    arm: LabelledArm
    pairs: int
    estimates: VariantEstimates
    #: Holm-adjusted across the variants; ``None`` where the test paired nothing.
    p_values: Mapping[PairedTest, float | None]
    decision: VariantDecision


@dataclass(frozen=True, slots=True)
class Comparison:
    """Every variant against one baseline, under the band its A/A pair sets."""

    baseline: LabelledArm
    replicate: LabelledArm
    band: CorrectnessBand
    variants: tuple[VariantComparison, ...]
    completeness_arm: bool


def check_variant_count(count: int) -> None:
    """Refuse a family Holm is not asked to adjust: none, or more than :data:`MAX_VARIANTS`.

    Example:
        >>> try:
        ...     check_variant_count(4)
        ... except ComparisonInputError as refused:
        ...     print(refused)
        4 variants, expected 1 to 3 against one baseline
    """
    if not 1 <= count <= MAX_VARIANTS:
        raise ComparisonInputError(
            f"{count} variants, expected 1 to {MAX_VARIANTS} against one baseline"
        )


def compare_arms(
    baseline: LabelledArm,
    variants: Sequence[LabelledArm],
    *,
    replicate: LabelledArm,
    completeness_arm: bool = False,
) -> Comparison:
    """Pair every variant with ``baseline``, adjust across them, and decide each one.

    Example:
        >>> comparison = compare_arms(baseline, [variant], replicate=aa)  # doctest: +SKIP
        >>> comparison.variants[0].decision.verdict  # doctest: +SKIP
        <VariantVerdict.PASSED: 'PASS'>
    """
    check_variant_count(len(variants))
    band = correctness_band(baseline.metrics, replicate.metrics)
    p_values = _holm_across([_raw_p_values(baseline.metrics, arm.metrics) for arm in variants])
    compared = tuple(
        _compared(baseline.metrics, arm, band, adjusted, completeness_arm=completeness_arm)
        for arm, adjusted in zip(variants, p_values, strict=True)
    )
    return Comparison(baseline, replicate, band, compared, completeness_arm)


def correctness_band(baseline: ArmMetrics, replicate: ArmMetrics) -> CorrectnessBand:
    """The band each correctness guard may fall by: ``max(|A1 − A2|, one task)``.

    Example:
        >>> correctness_band(baseline, replicate).needle_cited  # doctest: +SKIP
        0.1
    """
    return CorrectnessBand(
        needle_cited=_band(needle_cited_of_task, baseline, replicate),
        gold_site_coverage=_band(gold_site_coverage_of_task, baseline, replicate),
    )


def _band(read: TaskValue, baseline: ArmMetrics, replicate: ArmMetrics) -> float | None:
    """One task, widened to the A/A difference; ``None`` where the pair defines nothing."""
    pair = _point_pair(read, baseline, replicate)
    if pair is None:
        return None
    return max(abs(pair.delta), 1 / len(baseline.values_by_task(read)))


def _point_pair(read: TaskValue, baseline: ArmMetrics, variant: ArmMetrics) -> PointPair | None:
    """Both arms' means over the tasks both defined; ``None`` when they share none."""
    before, after = paired_values(read, baseline, variant)
    return PointPair(fmean(before), fmean(after)) if before else None


def _compared(
    baseline: ArmMetrics,
    arm: LabelledArm,
    band: CorrectnessBand,
    p_values: Mapping[PairedTest, float | None],
    *,
    completeness_arm: bool,
) -> VariantComparison:
    estimates = _estimates(baseline, arm.metrics)
    return VariantComparison(
        arm=arm,
        pairs=len(_task_ids(baseline) & _task_ids(arm.metrics)),
        estimates=estimates,
        p_values=MappingProxyType(dict(p_values)),
        decision=decide_variant(estimates, band, completeness_arm=completeness_arm),
    )


def _task_ids(arm: ArmMetrics) -> set[str]:
    return {task.task_id for task in arm.per_task}


def _estimates(baseline: ArmMetrics, variant: ArmMetrics) -> VariantEstimates:
    """The paired point estimates the acceptance rule reads."""
    return VariantEstimates(
        penalised_turns=_point_pair(penalised_turns_of_task, baseline, variant),
        budget_exhausted=_point_pair(budget_exhausted_of_task, baseline, variant),
        needle_cited=_point_pair(needle_cited_of_task, baseline, variant),
        gold_site_coverage=_point_pair(gold_site_coverage_of_task, baseline, variant),
    )


# One paired test's statistic: the two arms' paired values in, a p-value out.
_PairedStatistic = Callable[[Sequence[float], Sequence[float]], float | None]


def _lowered_p(before: Sequence[float], after: Sequence[float]) -> float | None:
    """The report's one-sided p that the variant LOWERED the value (turns, calls)."""
    return improvement_p(MetricDirection.LOWER_IS_BETTER, before, after)


def _mcnemar_p(before: Sequence[float], after: Sequence[float]) -> float:
    """The report's McNemar exact two-sided p on a paired 0/1 outcome."""
    return mcnemar_contrast(before, after).p_value


_PAIRED_TESTS: Mapping[PairedTest, tuple[TaskValue, _PairedStatistic]] = MappingProxyType(
    {
        PairedTest.TURNS: (penalised_turns_of_task, _lowered_p),
        PairedTest.CALLS: (tool_calls_of_task, _lowered_p),
        PairedTest.NEEDLE_REACHED: (gold_reached_of_task, _mcnemar_p),
        PairedTest.NEEDLE_CITED: (needle_cited_of_task, _mcnemar_p),
    }
)


def _raw_p_values(baseline: ArmMetrics, variant: ArmMetrics) -> dict[PairedTest, float | None]:
    """Each paired test's unadjusted p; ``None`` where it paired no task."""
    raw: dict[PairedTest, float | None] = {}
    for test, (read, statistic) in _PAIRED_TESTS.items():
        before, after = paired_values(read, baseline, variant)
        raw[test] = statistic(before, after) if before else None
    return raw


def _holm_across(
    raw: Sequence[Mapping[PairedTest, float | None]],
) -> list[dict[PairedTest, float | None]]:
    """Holm-adjust each test across the variants that defined it."""
    adjusted: list[dict[PairedTest, float | None]] = [dict(each) for each in raw]
    for test in PairedTest:
        defined = [(index, p) for index, each in enumerate(raw) if (p := each[test]) is not None]
        family = holm_adjust([p for _index, p in defined])
        for (index, _raw_p), adjusted_p in zip(defined, family, strict=True):
            adjusted[index][test] = adjusted_p
    return adjusted


__all__ = (
    "MAX_VARIANTS",
    "Comparison",
    "ComparisonInputError",
    "LabelledArm",
    "PairedTest",
    "VariantComparison",
    "check_variant_count",
    "compare_arms",
    "correctness_band",
)
