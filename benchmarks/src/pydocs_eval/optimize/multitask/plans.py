"""Run plans for a multi-dataset row pool — the ACROSS-runs axis.

Orthogonal to :mod:`sampling`: a plan decides how many training runs happen and
how they chain; a sampler decides what one run's batches look like. They
compose, so an experiment names both (``single × stratified``,
``curriculum × uniform``, …) and the two axes can be attributed separately.

* ``single`` — one run over the merged pool. The CONTROL.
* ``per_dataset`` — one run per task type, every run branching from the SAME
  seed, then a PER-SLOT merge. Each run specialises; the merge is the hard part.
* ``curriculum`` — sequential runs, each seeded with the previous run's best.
  Cheap to arrange and order-dependent by construction, which is exactly the
  risk being measured (later datasets can overwrite earlier behaviour).

Guidance flows through the plans as SECTION MAPPINGS (run-contract design
§9 stage 1, docs/superpowers/specs/2026-07-27-harness-run-contract-design.md):
the optimizer's unit of text is a named slot, whatever artifact
family rendered it. Free-form skill text (the usage_skill family) rides as a
single-slot mapping under :data:`FREE_FORM_GUIDANCE_KEY`. The old flat-string
merge died because concatenating two rendered section documents produces
duplicate ``=== HEADER ===`` lines, which the strict product grammar rejects
(``DuplicateSectionError``) — per-slot merging is the structural fix, and for
the single-slot free-form case it reproduces the old concatenation byte for
byte inside the slot.

A plan drives an injected ``train`` callable rather than SkillOpt itself, so
every arm is exercised offline for free — the discipline ``skillopt.py`` already
keeps with its monkeypatchable ``_invoke_train``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from pydocs_eval.optimize._prefix_report import mean_score_by_task_prefix
from pydocs_eval.registries import _Registry

__all__ = [
    "FREE_FORM_GUIDANCE_KEY",
    "PlanOutcome",
    "RunPlan",
    "TrainRequest",
    "TrainResult",
    "build_plan",
    "plan_registry",
]

Row = Mapping[str, object]
GuidanceSections = Mapping[str, str]

#: The single slot free-form (non-delimited) skill text rides under. Callers
#: wrap plain text as ``{FREE_FORM_GUIDANCE_KEY: text}`` and unwrap the same
#: key from the outcome; sectioned artifacts use their own header keys.
FREE_FORM_GUIDANCE_KEY = "SKILL"

#: Separator when two runs mutate the SAME slot. Joining is deliberately
#: dumb and happens INSIDE the slot: it is lossless and reviewable, whereas
#: an LLM merge would silently rewrite trained content and make the arm
#: impossible to attribute — and a document-level join is structurally
#: illegal for sectioned guidance (duplicate headers).
_MERGE_SEPARATOR = "\n\n"


@dataclass(frozen=True, slots=True)
class TrainRequest:
    """One training run's inputs."""

    seed_guidance_sections: GuidanceSections
    rows: tuple[Row, ...]
    task_types: tuple[str, ...]
    #: Names the run in output dirs and provenance (``"all"``, or a task type).
    label: str


@dataclass(frozen=True, slots=True)
class TrainResult:
    """One training run's outputs; ``scores`` maps task_id → score."""

    guidance_sections: GuidanceSections
    label: str
    scores: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanOutcome:
    """Every run of a plan plus the final guidance and per-type breakdown."""

    plan: str
    guidance_sections: GuidanceSections
    runs: tuple[TrainResult, ...]
    score_by_type: Mapping[str, float]


TrainFn = Callable[[TrainRequest], TrainResult]


class RunPlan(Protocol):
    """Executes some number of training runs over ``rows``."""

    def execute(
        self, seed_guidance_sections: GuidanceSections, rows: Sequence[Row], train: TrainFn
    ) -> PlanOutcome: ...


plan_registry: _Registry[RunPlan] = _Registry()


def build_plan(name: str, **kwargs: object) -> RunPlan:
    """Build the registered plan ``name`` with its keyword knobs."""
    return plan_registry.build(name, **kwargs)


def _task_types(rows: Sequence[Row]) -> tuple[str, ...]:
    return tuple(sorted({str(row.get("task_type", "other")) for row in rows}))


def _require_rows(rows: Sequence[Row]) -> None:
    if not rows:
        raise ValueError("cannot plan training over an empty row pool")


def _merge_guidance_sections(runs: Sequence[TrainResult]) -> GuidanceSections:
    """Per-slot merge: distinct slots stay disjoint; same-slot texts join.

    Run order fixes both slot order and same-slot join order, so the merge
    is deterministic and every trained text survives verbatim.
    """
    merged: dict[str, str] = {}
    for run in runs:
        for slot_name, text in run.guidance_sections.items():
            if slot_name in merged:
                merged[slot_name] = f"{merged[slot_name]}{_MERGE_SEPARATOR}{text}"
            else:
                merged[slot_name] = text
    return MappingProxyType(merged)


def _outcome(plan: str, guidance: GuidanceSections, runs: Sequence[TrainResult]) -> PlanOutcome:
    """Bundle runs into an outcome, folding every run's scores per task type.

    The per-type breakdown is the whole point of the comparison: a blended mean
    lets a 5%-of-the-pool dataset regress invisibly behind an improving average.
    """
    merged: dict[str, float] = {}
    for run in runs:
        merged.update(run.scores)
    # WHY the copy-proxy: a trainer that reuses or mutates its result dict
    # must not retroactively rewrite a recorded outcome (the pre-reshape
    # str field was immutable; the mapping must stay effectively so).
    return PlanOutcome(
        plan=plan,
        guidance_sections=MappingProxyType(dict(guidance)),
        runs=tuple(runs),
        score_by_type=mean_score_by_task_prefix(merged),
    )


@plan_registry.register("single")
@dataclass(frozen=True, slots=True)
class SinglePlan:
    """One run over the merged pool — today's behaviour, the control arm."""

    def execute(
        self, seed_guidance_sections: GuidanceSections, rows: Sequence[Row], train: TrainFn
    ) -> PlanOutcome:
        _require_rows(rows)
        result = train(
            TrainRequest(
                seed_guidance_sections=seed_guidance_sections,
                rows=tuple(rows),
                task_types=_task_types(rows),
                label="all",
            )
        )
        return _outcome("single", result.guidance_sections, [result])


@plan_registry.register("per_dataset")
@dataclass(frozen=True, slots=True)
class PerDatasetPlan:
    """One independent run per task type, then a per-slot merge.

    Every run branches from the SAME seed — that independence is what makes this
    a fan-out rather than a curriculum, and it is why the arm cannot suffer the
    ordering effects the curriculum arm is exposed to. The merge is per slot
    (see :func:`_merge_guidance_sections`).
    """

    def execute(
        self, seed_guidance_sections: GuidanceSections, rows: Sequence[Row], train: TrainFn
    ) -> PlanOutcome:
        _require_rows(rows)
        runs = [
            train(
                TrainRequest(
                    seed_guidance_sections=seed_guidance_sections,
                    rows=tuple(r for r in rows if str(r.get("task_type", "other")) == task_type),
                    task_types=(task_type,),
                    label=task_type,
                )
            )
            for task_type in _task_types(rows)
        ]
        return _outcome("per_dataset", _merge_guidance_sections(runs), runs)


@plan_registry.register("curriculum")
@dataclass(frozen=True, slots=True)
class CurriculumPlan:
    """Sequential runs, each seeded with the previous run's best guidance.

    ``order`` pins which dataset is trained first; it defaults to sorted task
    types. Making it explicit is deliberate — this arm is order-dependent by
    construction (a later dataset can overwrite behaviour an earlier one
    taught), so the order has to be part of the recorded experiment rather than
    an accident of dict iteration.
    """

    order: tuple[str, ...] = ()

    def execute(
        self, seed_guidance_sections: GuidanceSections, rows: Sequence[Row], train: TrainFn
    ) -> PlanOutcome:
        _require_rows(rows)
        present = _task_types(rows)
        sequence = self.order or present
        unknown = sorted(set(sequence) - set(present))
        if unknown:
            raise ValueError(
                f"curriculum order names task type(s) {unknown} absent from the pool; "
                f"present types are {list(present)}"
            )
        # An order that omits a present type trains on a strict subset of the pool
        # while still being reported as a curriculum result — the arm would look
        # like it covered both datasets when it silently dropped one.
        omitted = sorted(set(present) - set(sequence))
        if omitted:
            raise ValueError(
                f"curriculum order omits task type(s) {omitted} present in the pool; "
                "list every type explicitly (order fixes the sequence, not the "
                "membership) or those rows are silently never trained on"
            )

        runs: list[TrainResult] = []
        seed = seed_guidance_sections
        for task_type in sequence:
            result = train(
                TrainRequest(
                    seed_guidance_sections=seed,
                    rows=tuple(r for r in rows if str(r.get("task_type", "other")) == task_type),
                    task_types=(task_type,),
                    label=task_type,
                )
            )
            runs.append(result)
            seed = result.guidance_sections
        return _outcome("curriculum", runs[-1].guidance_sections, runs)
