"""Report aggregation for the ``ask_rubric`` fitness — sample records → ladder view.

Split out of ``ask_rubric`` so that module owns ONE thing: running and scoring a
sample. Everything here is a pure function of the recorded
``SampleRubricRecord`` list — no runner, no judge, no ledger — which is also why
it is testable without a rollout.
"""

from __future__ import annotations

import math
from statistics import fmean

from pydocs_eval.optimize._types import FitnessReport
from pydocs_eval.optimize.rubric.model import SampleRubricRecord


def build_fitness_report(
    records: list[SampleRubricRecord],
    *,
    fresh_cost: float,
    configured_criteria: tuple[str, ...] = (),
) -> FitnessReport:
    """Aggregate sample records into the ladder-facing report (AC-13)."""
    admitted = [r for r in records if r.discarded is None]
    score = fmean(r.verdict for r in admitted) if admitted else -math.inf
    components: dict[str, float] = {
        "gate_pass_rate": fmean(r.gate_pass_fraction for r in records) if records else 0.0,
        "judge_skip_rate": fmean(float(r.judge_skipped) for r in records) if records else 0.0,
        "judge_calls": float(
            sum(1 for r in records if not r.judge_skipped and (r.criteria or r.discarded))
        ),
        "discards": float(len(records) - len(admitted)),
        "turns_mean": fmean(r.turns for r in admitted) if admitted else 0.0,
        "wall_seconds_mean": fmean(r.wall_seconds for r in admitted) if admitted else 0.0,
    }
    components.update(_criterion_means(admitted, configured_criteria))
    components.update(_gate_rates(records))
    components.update(_check_means(records))
    return FitnessReport(
        score=score, components=components, cost_usd=fresh_cost, n_samples=len(admitted)
    )


def _criterion_means(
    admitted: list[SampleRubricRecord], configured: tuple[str, ...]
) -> dict[str, float]:
    # WHY the configured union: AC-13 promises EVERY criterion.<name>_mean —
    # a rung where every sample was gate-skipped still reports the keys
    # (0.0) instead of silently dropping them.
    names = sorted(set(configured) | {name for r in admitted for name in r.criteria})
    return {
        f"criterion.{name}_mean": (
            fmean(r.criteria[name] for r in admitted if name in r.criteria)
            if any(name in r.criteria for r in admitted)
            else 0.0
        )
        for name in names
    }


def _gate_rates(records: list[SampleRubricRecord]) -> dict[str, float]:
    names = sorted({name for r in records for name in r.gates})
    return {
        f"gate.{name}_rate": fmean(float(r.gates[name]) for r in records if name in r.gates)
        for name in names
    }


def _check_means(records: list[SampleRubricRecord]) -> dict[str, float]:
    """Per-check means — how the deterministic composite was actually earned.

    Absent entirely for a gates-only objective, so no report component appears
    that was not configured.
    """
    names = sorted({name for r in records for name in r.checks})
    return {
        f"check.{name}_mean": fmean(r.checks[name] for r in records if name in r.checks)
        for name in names
    }
