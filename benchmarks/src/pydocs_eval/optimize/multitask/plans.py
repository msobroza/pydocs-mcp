"""Run plans for a multi-dataset row pool — the ACROSS-runs axis.

Orthogonal to :mod:`sampling`: a plan decides how many training runs happen and
how they chain; a sampler decides what one run's batches look like. They
compose, so an experiment names both (``single × stratified``,
``curriculum × uniform``, …) and the two axes can be attributed separately.

* ``single`` — one run over the merged pool. The CONTROL.
* ``per_dataset`` — one run per task type, every run branching from the SAME
  seed, then merge. Each skill specialises; the merge is the hard part.
* ``curriculum`` — sequential runs, each seeded with the previous run's best.
  Cheap to arrange and order-dependent by construction, which is exactly the
  risk being measured (later datasets can overwrite earlier behaviour).

A plan drives an injected ``train`` callable rather than SkillOpt itself, so
every arm is exercised offline for free — the discipline ``skillopt.py`` already
keeps with its monkeypatchable ``_invoke_train``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pydocs_eval.optimize._prefix_report import mean_score_by_task_prefix
from pydocs_eval.registries import _Registry

__all__ = [
    "PlanOutcome",
    "RunPlan",
    "TrainRequest",
    "TrainResult",
    "build_plan",
    "plan_registry",
]

Row = Mapping[str, object]

#: Separator between merged per-dataset skills. Concatenation is deliberately
#: dumb: it is lossless and reviewable, whereas an LLM merge would silently
#: rewrite trained content and make the arm impossible to attribute.
_MERGE_SEPARATOR = "\n\n"


@dataclass(frozen=True, slots=True)
class TrainRequest:
    """One training run's inputs."""

    seed_skill: str
    rows: tuple[Row, ...]
    task_types: tuple[str, ...]
    #: Names the run in output dirs and provenance (``"all"``, or a task type).
    label: str


@dataclass(frozen=True, slots=True)
class TrainResult:
    """One training run's outputs; ``scores`` maps task_id → score."""

    skill: str
    label: str
    scores: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanOutcome:
    """Every run of a plan plus the final skill and its per-type breakdown."""

    plan: str
    skill: str
    runs: tuple[TrainResult, ...]
    score_by_type: Mapping[str, float]


TrainFn = Callable[[TrainRequest], TrainResult]


class RunPlan(Protocol):
    """Executes some number of training runs over ``rows``."""

    def execute(self, seed_skill: str, rows: Sequence[Row], train: TrainFn) -> PlanOutcome: ...


plan_registry: _Registry[RunPlan] = _Registry()


def build_plan(name: str, **kwargs: object) -> RunPlan:
    """Build the registered plan ``name`` with its keyword knobs."""
    return plan_registry.build(name, **kwargs)


def _task_types(rows: Sequence[Row]) -> tuple[str, ...]:
    return tuple(sorted({str(row.get("task_type", "other")) for row in rows}))


def _require_rows(rows: Sequence[Row]) -> None:
    if not rows:
        raise ValueError("cannot plan training over an empty row pool")


def _outcome(plan: str, skill: str, runs: Sequence[TrainResult]) -> PlanOutcome:
    """Bundle runs into an outcome, folding every run's scores per task type.

    The per-type breakdown is the whole point of the comparison: a blended mean
    lets a 5%-of-the-pool dataset regress invisibly behind an improving average.
    """
    merged: dict[str, float] = {}
    for run in runs:
        merged.update(run.scores)
    return PlanOutcome(
        plan=plan,
        skill=skill,
        runs=tuple(runs),
        score_by_type=mean_score_by_task_prefix(merged),
    )


@plan_registry.register("single")
@dataclass(frozen=True, slots=True)
class SinglePlan:
    """One run over the merged pool — today's behaviour, the control arm."""

    def execute(self, seed_skill: str, rows: Sequence[Row], train: TrainFn) -> PlanOutcome:
        _require_rows(rows)
        result = train(
            TrainRequest(
                seed_skill=seed_skill,
                rows=tuple(rows),
                task_types=_task_types(rows),
                label="all",
            )
        )
        return _outcome("single", result.skill, [result])


@plan_registry.register("per_dataset")
@dataclass(frozen=True, slots=True)
class PerDatasetPlan:
    """One independent run per task type, then concatenate the skills.

    Every run branches from the SAME seed — that independence is what makes this
    a fan-out rather than a curriculum, and it is why the arm cannot suffer the
    ordering effects the curriculum arm is exposed to. The merge is plain
    concatenation (see :data:`_MERGE_SEPARATOR`).
    """

    def execute(self, seed_skill: str, rows: Sequence[Row], train: TrainFn) -> PlanOutcome:
        _require_rows(rows)
        runs = [
            train(
                TrainRequest(
                    seed_skill=seed_skill,
                    rows=tuple(r for r in rows if str(r.get("task_type", "other")) == task_type),
                    task_types=(task_type,),
                    label=task_type,
                )
            )
            for task_type in _task_types(rows)
        ]
        return _outcome("per_dataset", _MERGE_SEPARATOR.join(r.skill for r in runs), runs)


@plan_registry.register("curriculum")
@dataclass(frozen=True, slots=True)
class CurriculumPlan:
    """Sequential runs, each seeded with the previous run's best skill.

    ``order`` pins which dataset is trained first; it defaults to sorted task
    types. Making it explicit is deliberate — this arm is order-dependent by
    construction (a later dataset can overwrite behaviour an earlier one
    taught), so the order has to be part of the recorded experiment rather than
    an accident of dict iteration.
    """

    order: tuple[str, ...] = ()

    def execute(self, seed_skill: str, rows: Sequence[Row], train: TrainFn) -> PlanOutcome:
        _require_rows(rows)
        present = _task_types(rows)
        sequence = self.order or present
        unknown = sorted(set(sequence) - set(present))
        if unknown:
            raise ValueError(
                f"curriculum order names task type(s) {unknown} absent from the pool; "
                f"present types are {list(present)}"
            )

        runs: list[TrainResult] = []
        skill = seed_skill
        for task_type in sequence:
            result = train(
                TrainRequest(
                    seed_skill=skill,
                    rows=tuple(r for r in rows if str(r.get("task_type", "other")) == task_type),
                    task_types=(task_type,),
                    label=task_type,
                )
            )
            runs.append(result)
            skill = result.skill
        return _outcome("curriculum", skill, runs)
