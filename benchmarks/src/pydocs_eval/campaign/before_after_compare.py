"""Up to three variant arms against one baseline: Holm-adjusted p, the band, and a verdict.

The instrument every paid acceptance reads (#372). Each variant is paired with
the baseline by task id, over the tasks both defined (ADR 0016 §Statistics),
and gets:

- the one-sided Wilcoxon signed-rank p that it lowered the penalised
  turns-to-answer and the tool calls per task, and McNemar's exact two-sided p
  on Needle reached and ``needle cited``, each Holm-adjusted across the (at
  most :data:`MAX_VARIANTS`) variants, so no variant reads more significant
  than the family allows;
- the point estimates the acceptance rule reads, and its verdict
  (``before_after_acceptance``).

The correctness band is the baseline's own: the larger of the A/A pair's
difference (a second baseline arm on the same commit and settings) and one
task, on ``needle cited`` and, over the multi-site tasks, gold-site coverage.
Without an A/A arm the band is the one-task floor.

Example:
    >>> compare_arms(baseline, [variant]).variants[0].decision.verdict  # doctest: +SKIP
    <Verdict.PASSED: 'PASS'>
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
    Decision,
    PointPair,
    VariantEstimates,
    decide,
)
from pydocs_eval.campaign.before_after_measure import ArmMetrics, paired_values
from pydocs_eval.campaign.before_after_task_measurement import TaskMeasurement, TaskValue
from pydocs_eval.metrics.aggregate import (
    holm_adjust,
    mcnemar_exact_p,
    wilcoxon_signed_rank_p_one_sided,
)

#: Holm's family: the spec caps a comparison at three variants against one baseline.
MAX_VARIANTS = 3


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
    decision: Decision


@dataclass(frozen=True, slots=True)
class Comparison:
    """Every variant against one baseline, under one band."""

    baseline: LabelledArm
    replicate: LabelledArm | None
    band: CorrectnessBand
    variants: tuple[VariantComparison, ...]
    completeness_arm: bool


def _penalised_turns(task: TaskMeasurement) -> float | None:
    return task.ending.turns_to_answer_penalised


def _tool_calls(task: TaskMeasurement) -> float | None:
    return task.usage.tool_calls_total


def _needle_reached(task: TaskMeasurement) -> float | None:
    return task.reached_gold


def _needle_cited(task: TaskMeasurement) -> float | None:
    return task.answer.needle_cited


def _budget_exhausted(task: TaskMeasurement) -> float | None:
    return task.ending.budget_exhausted


def _gold_site_coverage(task: TaskMeasurement) -> float | None:
    return task.answer.gold_site_coverage


def check_variant_count(count: int) -> None:
    """Refuse a family Holm is not asked to adjust: none, or more than :data:`MAX_VARIANTS`."""
    if not 1 <= count <= MAX_VARIANTS:
        raise MeasurementPlanError(
            f"{count} variants, expected 1 to {MAX_VARIANTS} against one baseline"
        )


def compare_arms(
    baseline: LabelledArm,
    variants: Sequence[LabelledArm],
    *,
    replicate: LabelledArm | None = None,
    completeness_arm: bool = False,
) -> Comparison:
    """Pair every variant with ``baseline``, adjust across them, and decide each one."""
    check_variant_count(len(variants))
    band = correctness_band(baseline.metrics, None if replicate is None else replicate.metrics)
    p_values = _holm_across([_raw_p_values(baseline.metrics, arm.metrics) for arm in variants])
    compared = tuple(
        _compared(baseline.metrics, arm, band, adjusted, completeness_arm=completeness_arm)
        for arm, adjusted in zip(variants, p_values, strict=True)
    )
    return Comparison(baseline, replicate, band, compared, completeness_arm)


def correctness_band(baseline: ArmMetrics, replicate: ArmMetrics | None) -> CorrectnessBand:
    """The band each correctness guard may fall by: ``max(|A1 − A2|, one task)``."""
    return CorrectnessBand(
        needle_cited=_band(_needle_cited, baseline, replicate),
        gold_site_coverage=_band(_gold_site_coverage, baseline, replicate),
    )


def _band(read: TaskValue, baseline: ArmMetrics, replicate: ArmMetrics | None) -> float | None:
    """One task, widened to the A/A difference; ``None`` where the baseline defines nothing."""
    defined = baseline.values_by_task(read)
    if not defined:
        return None
    one_task = 1 / len(defined)
    pair = None if replicate is None else _point_pair(read, baseline, replicate)
    return one_task if pair is None else max(abs(pair.delta), one_task)


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
        p_values=p_values,
        decision=decide(estimates, band, completeness_arm=completeness_arm),
    )


def _task_ids(arm: ArmMetrics) -> set[str]:
    return {task.task_id for task in arm.per_task}


def _estimates(baseline: ArmMetrics, variant: ArmMetrics) -> VariantEstimates:
    """The paired point estimates the acceptance rule reads."""
    return VariantEstimates(
        penalised_turns=_point_pair(_penalised_turns, baseline, variant),
        budget_exhausted=_point_pair(_budget_exhausted, baseline, variant),
        needle_cited=_point_pair(_needle_cited, baseline, variant),
        gold_site_coverage=_point_pair(_gold_site_coverage, baseline, variant),
    )


# A paired test: the two arms' paired values in, a p-value out.
_Test = Callable[[Sequence[float], Sequence[float]], float]


def _lowered_p(before: Sequence[float], after: Sequence[float]) -> float:
    """One-sided signed-rank p that the variant LOWERED the value."""
    return wilcoxon_signed_rank_p_one_sided([b - a for b, a in zip(before, after, strict=True)])


def _mcnemar_p(before: Sequence[float], after: Sequence[float]) -> float:
    """McNemar's exact two-sided p on a paired 0/1 outcome — the report's own test."""
    pairs = list(zip(before, after, strict=True))
    gained = sum(1 for was, now in pairs if now and not was)
    lost = sum(1 for was, now in pairs if was and not now)
    return mcnemar_exact_p(gained, lost)


_PAIRED_TESTS: Mapping[PairedTest, tuple[TaskValue, _Test]] = MappingProxyType(
    {
        PairedTest.TURNS: (_penalised_turns, _lowered_p),
        PairedTest.CALLS: (_tool_calls, _lowered_p),
        PairedTest.NEEDLE_REACHED: (_needle_reached, _mcnemar_p),
        PairedTest.NEEDLE_CITED: (_needle_cited, _mcnemar_p),
    }
)


def _raw_p_values(baseline: ArmMetrics, variant: ArmMetrics) -> dict[PairedTest, float | None]:
    """Each paired test's unadjusted p; ``None`` where it paired no task."""
    raw: dict[PairedTest, float | None] = {}
    for test, (read, p_value_of) in _PAIRED_TESTS.items():
        before, after = paired_values(read, baseline, variant)
        raw[test] = p_value_of(before, after) if before else None
    return raw


def _holm_across(
    raw: Sequence[Mapping[PairedTest, float | None]],
) -> list[dict[PairedTest, float | None]]:
    """Holm-adjust each test across the variants that defined it."""
    adjusted: list[dict[PairedTest, float | None]] = [dict(each) for each in raw]
    for test in PairedTest:
        defined = [(index, p) for index, each in enumerate(raw) if (p := each[test]) is not None]
        family = holm_adjust([p for _index, p in defined])
        for (index, _raw), held in zip(defined, family, strict=True):
            adjusted[index][test] = held
    return adjusted


__all__ = (
    "MAX_VARIANTS",
    "Comparison",
    "LabelledArm",
    "PairedTest",
    "VariantComparison",
    "check_variant_count",
    "compare_arms",
    "correctness_band",
)
