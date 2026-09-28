"""How two arms are paired, and the paired tests read off the pairs.

The before/after report (one paired row per metric) and the compare verb (one
verdict per variant) pair their arms by task id over the tasks both defined, and
test the pairs the same way, through this module: a report's p and a verdict's p
can never disagree. The statistics are ``metrics/aggregate.py``'s, CALLED and
never re-derived.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_eval.campaign.before_after_measure import ArmMetrics
from pydocs_eval.campaign.before_after_rows import MetricDirection
from pydocs_eval.campaign.before_after_task_measurement import TaskValue
from pydocs_eval.metrics.aggregate import mcnemar_from_pairs, wilcoxon_signed_rank_p_one_sided


def paired_values(
    read: TaskValue, baseline: ArmMetrics, candidate: ArmMetrics
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """The two arms' values over the tasks BOTH measured and both defined.

    Sorted by task id so a bootstrap over them resamples deterministically,
    matching ``mcnemar_from_pairs``'s own ordering rule.

    Example:
        >>> paired_values(lambda task: task.reached_gold, before, after)  # doctest: +SKIP
        ((0.0, 1.0), (1.0, 1.0))
    """
    before, after = baseline.values_by_task(read), candidate.values_by_task(read)
    shared = sorted(before.keys() & after.keys())
    return tuple(before[task_id] for task_id in shared), tuple(after[task_id] for task_id in shared)


def improvement_p(
    direction: MetricDirection, before: Sequence[float], after: Sequence[float]
) -> float | None:
    """One-sided p for "the candidate improved", in the METRIC's own direction.

    The signed-rank test is directional and reads a positive difference as
    favouring the candidate, so a lower-is-better metric must be differenced the
    other way round; feeding it raw ``after - before`` would report a
    needless-call rate that FELL as evidence against the candidate. A metric with
    no direction to improve in gets no one-sided test: ``None``.

    Example:
        >>> round(improvement_p(MetricDirection.LOWER_IS_BETTER, [6.0] * 6, [5.0] * 6), 4)
        0.0156
    """
    if direction is MetricDirection.NEUTRAL:
        return None
    lower_is_better = direction is MetricDirection.LOWER_IS_BETTER
    gains = [b - a if lower_is_better else a - b for b, a in zip(before, after, strict=True)]
    return wilcoxon_signed_rank_p_one_sided(gains)


@dataclass(frozen=True, slots=True)
class McNemarContrast:
    """A paired 0/1 outcome's change: candidate minus baseline, its 95% CI, and the p."""

    delta: float
    low: float
    high: float
    p_value: float


def mcnemar_contrast(before: Sequence[float], after: Sequence[float]) -> McNemarContrast:
    """The paired 2x2 on a 0/1 outcome: the change, its interval, and McNemar's exact p.

    ``hard_a`` is the CANDIDATE, so the delta reads candidate minus baseline, the
    same direction as every other row. The p is McNemar's two-sided exact: the
    report is not the ADR 0018 acceptance gate, and the campaign aggregator
    reports the same two-sided value.

    Example:
        >>> mcnemar_contrast([0.0, 0.0, 1.0], [1.0, 1.0, 1.0]).p_value
        0.5
    """
    keys = [str(index) for index in range(len(before))]
    _b, _c, _n, delta, p_value, (_, low, high) = mcnemar_from_pairs(
        {key: int(value) for key, value in zip(keys, after, strict=True)},
        {key: int(value) for key, value in zip(keys, before, strict=True)},
    )
    return McNemarContrast(delta=delta, low=low, high=high, p_value=p_value)


__all__ = ("McNemarContrast", "improvement_p", "mcnemar_contrast", "paired_values")
