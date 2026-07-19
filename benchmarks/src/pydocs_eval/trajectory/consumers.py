"""One derived computation → three consumer shapes (ADR 0012 — one computation).

The shaped score, taxonomy label, and feedback string are computed ONCE per
trajectory into a :class:`DerivedRecord`; the three optimizer consumers are pure
projections of that single record — no second implementation of any component
(R3):

- **SkillOpt** row ``{id, hard, soft}`` + ``fail_reason`` (``skillopt/envs/base.py``).
- **GEPA** pair ``(score: float, feedback: str)`` (``default_adapter.py``).
- **FitnessReport**-compatible per-run aggregate ``{score, components, cost_usd,
  n_samples}`` (``optimize/_types.py``), excluding ``infra_error`` rollouts from
  the aggregate (ADR 0012).

``hard`` is strict binary resolve; ``soft`` is the shaped score. This module owns
the assembly only — it does NOT own the gate (``gate.py`` is a separate,
import-isolated module, R4).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from pydocs_eval.trajectory.attribution import Attribution
from pydocs_eval.trajectory.eval_report import GroundTruthOutcome
from pydocs_eval.trajectory.feedback import build_feedback
from pydocs_eval.trajectory.metrics import TrajectoryMetrics
from pydocs_eval.trajectory.schema import LoopEvent, ToolEvent
from pydocs_eval.trajectory.shaped_score import ScoreWeights, compute_shaped_score
from pydocs_eval.trajectory.taxonomy import (
    TaxonomyConfig,
    TaxonomyInputs,
    classify,
)


@dataclass(frozen=True, slots=True)
class DerivedRecord:
    """The single derived computation for one trajectory (ADR 0012 R3).

    ``hard`` is strict binary resolve (1 iff the ground-truth outcome resolved);
    ``soft`` is the shaped score. ``fail_reason`` is the taxonomy label on a
    failure (``hard=0``) and empty on a resolve. ``excluded_from_aggregates`` is
    the ``infra_error`` carve-out.
    """

    trajectory_id: str
    instance_id: str
    hard: int
    soft: float
    components: dict[str, float]
    label: str
    feedback: str
    fail_reason: str
    cost_usd: float
    score_version: int
    taxonomy_version: int
    excluded_from_aggregates: bool

    def to_dict(self) -> dict[str, Any]:
        """Canonical-JSON-ready dict (stable key order for byte-identical goldens)."""
        return {
            "trajectory_id": self.trajectory_id,
            "instance_id": self.instance_id,
            "hard": self.hard,
            "soft": self.soft,
            "components": dict(self.components),
            "label": self.label,
            "feedback": self.feedback,
            "fail_reason": self.fail_reason,
            "cost_usd": self.cost_usd,
            "score_version": self.score_version,
            "taxonomy_version": self.taxonomy_version,
            "excluded_from_aggregates": self.excluded_from_aggregates,
        }


def _taxonomy_inputs(
    *,
    metrics: TrajectoryMetrics,
    attribution: Attribution,
    outcome: GroundTruthOutcome,
    events: Sequence[Any],
    gold_files: frozenset[str],
    final_patch_files: frozenset[str],
    patch_bytes: int,
    turn_cap: int | None,
) -> TaxonomyInputs:
    """Assemble the taxonomy decision-tree inputs from the parsed trajectory facts."""
    tokens = metrics.tokens
    return TaxonomyInputs(
        outcome=outcome,
        tool_events=tuple(e for e in events if isinstance(e, ToolEvent)),
        loop_events=tuple(e for e in events if isinstance(e, LoopEvent)),
        patch_bytes=patch_bytes,
        gold_surfaced=bool(attribution.surfaced_files & gold_files),
        patch_touches_gold=bool(final_patch_files & gold_files),
        f2p_fraction=metrics.f2p_fraction,
        p2p_regressions=metrics.p2p_regression_count,
        num_turns=metrics.turns,
        total_tokens=tokens.input_tokens + tokens.output_tokens,
        wall_seconds=metrics.wall_clock_seconds,
        turn_cap=turn_cap,
    )


def compute_derived_record(
    *,
    trajectory_id: str,
    instance_id: str,
    metrics: TrajectoryMetrics,
    attribution: Attribution,
    outcome: GroundTruthOutcome,
    events: Sequence[Any],
    gold_files: frozenset[str],
    gold_f2p: frozenset[str],
    final_patch_files: frozenset[str],
    patch_bytes: int,
    turn_cap: int | None,
    cost_usd: float,
    weights: ScoreWeights | None = None,
    config: TaxonomyConfig | None = None,
) -> DerivedRecord:
    """Compute the shaped score, taxonomy label, and feedback ONCE (ADR 0012 R3).

    ``events`` is the ordered tool+loop stream (needed for the empty/crash/
    never-ran-tests trace detectors); everything else is the parsed metric +
    attribution + eval facts. The three consumer emitters below project the
    returned record.
    """
    inputs = _taxonomy_inputs(
        metrics=metrics,
        attribution=attribution,
        outcome=outcome,
        events=events,
        gold_files=gold_files,
        final_patch_files=final_patch_files,
        patch_bytes=patch_bytes,
        turn_cap=turn_cap,
    )
    label = classify(inputs, config=config)
    scored = compute_shaped_score(metrics, outcome, turn_cap=turn_cap, weights=weights)
    feedback = build_feedback(
        label=label.label,
        metrics=metrics,
        attribution=attribution,
        outcome=outcome,
        gold_files=gold_files,
        gold_f2p=gold_f2p,
        turn_cap=turn_cap,
    )
    return DerivedRecord(
        trajectory_id=trajectory_id,
        instance_id=instance_id,
        hard=1 if outcome.resolved else 0,
        soft=scored.soft,
        components=scored.components,
        label=label.label,
        feedback=feedback,
        fail_reason="" if outcome.resolved else label.label,
        cost_usd=cost_usd,
        score_version=scored.score_version,
        taxonomy_version=label.taxonomy_version,
        excluded_from_aggregates=label.excluded_from_aggregates,
    )


# ---------------------------------------------------------------------------
# Consumer emitters — pure projections of one DerivedRecord
# ---------------------------------------------------------------------------


def skillopt_row(record: DerivedRecord) -> dict[str, Any]:
    """SkillOpt rollout row ``{id, hard, soft}`` + textual ``fail_reason``.

    Matches ``skillopt/envs/base.py:226-232`` plus the in-repo adapter's
    ``fail_reason`` field (which becomes a downstream consumer, ADR 0012).
    """
    return {
        "id": record.trajectory_id,
        "hard": record.hard,
        "soft": record.soft,
        "fail_reason": record.fail_reason,
    }


def gepa_pair(record: DerivedRecord) -> tuple[float, str]:
    """GEPA ``(score, feedback)`` pair (``default_adapter.py:17-20``)."""
    return record.soft, record.feedback


@dataclass(frozen=True, slots=True)
class RunAggregate:
    """FitnessReport-compatible per-run aggregate (``optimize/_types.py:26-38``).

    ``infra_error`` rollouts are excluded from ``score`` / ``components`` /
    ``n_samples`` (ADR 0012) and counted separately in ``infra_excluded``.
    ``to_fitness_report_dict`` is the exact ``FitnessReport(**d)`` shape.
    """

    score: float
    components: dict[str, float]
    cost_usd: float
    n_samples: int
    infra_excluded: int

    def to_fitness_report_dict(self) -> dict[str, Any]:
        """The four ``FitnessReport`` fields (infra count dropped — it's not scored)."""
        return {
            "score": self.score,
            "components": dict(self.components),
            "cost_usd": self.cost_usd,
            "n_samples": self.n_samples,
        }


def run_aggregate(records: Iterable[DerivedRecord]) -> RunAggregate:
    """Mean-aggregate soft score + components over graded (non-infra) records.

    ``cost_usd`` sums ALL records (infra rollouts still cost money); ``score`` /
    ``components`` / ``n_samples`` cover only graded rollouts. An all-infra (or
    empty) run yields a ``0.0`` score over ``0`` graded samples.
    """
    all_records = list(records)
    graded = [r for r in all_records if not r.excluded_from_aggregates]
    total_cost = sum(r.cost_usd for r in all_records)
    return RunAggregate(
        score=_mean(r.soft for r in graded) if graded else 0.0,
        components=_mean_components(graded),
        cost_usd=total_cost,
        n_samples=len(graded),
        infra_excluded=len(all_records) - len(graded),
    )


def _mean(values: Iterable[float]) -> float:
    """Arithmetic mean of a non-empty iterable."""
    items = list(values)
    return sum(items) / len(items)


def _mean_components(graded: list[DerivedRecord]) -> dict[str, float]:
    """Per-component mean across graded records (empty dict when none graded)."""
    if not graded:
        return {}
    keys = graded[0].components.keys()
    return {key: _mean(r.components[key] for r in graded) for key in keys}
