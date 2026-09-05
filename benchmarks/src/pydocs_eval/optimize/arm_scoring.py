"""The ``scoring:`` arm cell key — one objective, many observed metrics (design §6).

The seventh canonical cell key, and the only one that says what an arm's
numbers MEAN::

    scoring:
      objective: rubric_verdict     # the ONE metric the optimizer maximizes
      rubric: ask_rubric            # which configured objective that verdict is
      tracked: [gold_recall]        # observational metrics, recorded not optimized

**One objective, many metrics.** A dataset can carry several tasks and a task
can span several datasets, so arms sharing a ``task_name`` share their
``TASK_HEAD`` guidance across datasets while each arm binds its own metrics
here. An arm may WATCH any number of metrics; it may OPTIMIZE exactly one —
a second maximand is not a richer objective, it is an unstated trade-off, and
paired acceptance would no longer be testing a single hypothesis.

**The identity asymmetry** (recorded at the fold site in ``arms.py``): the
objective moves verdicts, so the resolved rubric hash and the objective kind
fold into arm identity; ``tracked`` moves nothing, so adding or removing an
observation must never invalidate an arm's resume state.

**``scoring`` is REQUIRED on every arm.** Explicit beats implicit for the
thing being maximized, and the ``arms:`` block is new enough that requiring it
costs no migration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ConfigDict, field_validator

from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.optimize.rubric.checks import (
    Check,
    check_registry,
    evaluate_check,
    required_params_for,
)
from pydocs_eval.optimize.rubric.gates import gate_registry

if TYPE_CHECKING:
    # WHY TYPE_CHECKING only: like the rubric core this module stays importable
    # on a base install — tracked metrics only READ attributes off the trajectory.
    from pydocs_mcp.harness.core.run_contract import Trajectory

__all__ = [
    "ArmScoring",
    "ObjectiveKind",
    "objective_fitness_name",
    "observation_checks",
    "observe_tracked_metrics",
    "resolve_rubric_section",
]


class ObjectiveKind(StrEnum):
    """The closed vocabulary of arm optimization objectives.

    Exactly one member today — the layered gate → rubric → verdict score the
    ``ask_rubric`` fitness composes. Adding a second is a measurement event,
    not a config edit: two objectives produce numbers that are not comparable
    on one ladder, so acceptance rules have to say which one ranks candidates.
    """

    RUBRIC_VERDICT = "rubric_verdict"


# WHICH registered fitness computes each objective — the ONE bridge between the
# ``scoring.objective`` vocabulary and the ladder's ``fitness_name`` namespace.
# Without it the two are related only by a string literal at the install site,
# and an arms config whose ladder names some other fitness loads clean, walks
# green and never measures its arms' declared objective even once.
_OBJECTIVE_FITNESS_NAMES: Mapping[ObjectiveKind, str] = {
    ObjectiveKind.RUBRIC_VERDICT: "ask_rubric",
}


def objective_fitness_name(objective: ObjectiveKind) -> str:
    """The registered fitness that computes ``objective``.

    Raises:
        KeyError: the objective has no fitness bound (a new ``ObjectiveKind``
            member without a row above — the error names both).
    """
    name = _OBJECTIVE_FITNESS_NAMES.get(objective)
    if name is None:
        raise KeyError(
            f"objective {str(objective)!r} has no fitness bound; "
            f"have {sorted(str(k) for k in _OBJECTIVE_FITNESS_NAMES)}"
        )
    return name


# The pure-observation policy, spelled once (see the role table in
# ``rubric/checks.py``): weight 0 keeps a tracked metric out of the composite's
# numerator AND its renormalizing denominator, ``required=False`` + ``fail=None``
# keep it out of ``fail_fast``. So an observation is recorded and nothing else.
_OBSERVATION_WEIGHT = 0.0
_OBSERVATION_REQUIRED = False
_OBSERVATION_FAIL: float | None = None


def _known_metric_kinds() -> frozenset[str]:
    """Every REGISTERED metric name — scored checks plus gates.

    Both registries, because ``evaluate_check`` already adapts a boolean gate
    to a 0-1 score: a gate kind is observable with no porting.
    """
    return frozenset(check_registry.names()) | frozenset(gate_registry.names())


def _observable_kinds() -> frozenset[str]:
    """Every name a ``tracked`` entry may spell — registered AND params-free.

    Registered is not enough. ``observation_checks`` mints every tracked metric
    with NO params (that IS the pure-observation shape), so a kind whose
    predicate requires one — ``answer_regex`` needs ``params["pattern"]`` —
    would load clean and raise ``KeyError`` at MEASUREMENT time: after the
    rollout and the judge call were already paid for, and before the ledger
    line a rerun could resume from. The firewall this key belongs to promises
    the opposite (validation fires at load time, never at trial 14), so the
    params-free assumption is enforced here rather than assumed.
    """
    return frozenset(kind for kind in _known_metric_kinds() if not required_params_for(kind))


def _require_observable(name: str) -> None:
    """Raise unless ``name`` is a registered, params-free metric kind.

    Raises:
        ValueError: naming the offending value, why it is not observable, and
            the kinds that would have worked.
    """
    observable = _observable_kinds()
    if name in observable:
        return
    needed = required_params_for(name) if name in _known_metric_kinds() else ()
    if needed:
        raise ValueError(
            f"invalid tracked {name!r}: its predicate requires params {list(needed)}, and an "
            "observational metric is measured with NO params — observable kinds are "
            f"{sorted(observable)}"
        )
    raise ValueError(
        f"invalid tracked {name!r}: an observational metric must name a registered, "
        f"params-free check or gate kind — have {sorted(observable)}"
    )


class ArmScoring(BaseModel):
    """What ONE arm optimizes, and what it merely watches (design §6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: ObjectiveKind
    #: Names a rubric objective configured in this run config; resolved (and
    #: hashed into arm identity) by ``run_config.arm_objective_hash``.
    rubric: str
    tracked: tuple[str, ...] = ()

    @field_validator("tracked")
    @classmethod
    def _check_tracked_kinds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require every observational metric to be registered AND params-free."""
        for name in value:
            _require_observable(name)
        duplicates = tuple(name for i, name in enumerate(value) if name in value[:i])
        if duplicates:
            raise ValueError(
                f"invalid tracked {list(value)}: duplicate {list(duplicates)} — outcomes "
                "are keyed by name, so a duplicate silently drops one measurement"
            )
        return value


_RubricSection = TypeVar("_RubricSection")


def resolve_rubric_section(
    scoring: ArmScoring,
    *,
    available: Mapping[str, _RubricSection],
    arm_label: str,
) -> _RubricSection:
    """Resolve ``scoring.rubric`` against the run config's rubric sections.

    Name-level only, and generic over the section type so this module never
    imports the run config that owns it (that import runs the other way).

    Raises:
        ValueError: the name matches no configured section, naming the arm,
            the offending value, and the names that would have worked.
    """
    if not available:
        raise ValueError(
            f"{arm_label} scoring.rubric names {scoring.rubric!r}, but this run configures no "
            "rubric objective at all — add an `ask_rubric:` section to the config (an arm "
            "binds exactly one objective and it must be spelled in the same config)"
        )
    if scoring.rubric not in available:
        raise ValueError(
            f"{arm_label} scoring.rubric names {scoring.rubric!r}; the rubric objectives "
            f"configured in this run are {sorted(available)} — an arm binds exactly one "
            "objective and it must be spelled in the same config"
        )
    return available[scoring.rubric]


def observation_checks(tracked: Sequence[str]) -> tuple[Check, ...]:
    """Tracked metric names as pure-observation checks (weight 0, never blocking).

    Takes the NAMES rather than the whole ``ArmScoring`` so the fitness — which
    is handed one arm's tracked list, not its cell — uses the same one builder.
    Minting with NO params is the pure-observation shape, and it is why
    ``ArmScoring`` admits only params-free kinds (``_observable_kinds``).

    Example:
        >>> observation_checks(())
        ()
    """
    return tuple(
        Check(
            name=kind,
            kind=kind,
            weight=_OBSERVATION_WEIGHT,
            required=_OBSERVATION_REQUIRED,
            fail=_OBSERVATION_FAIL,
        )
        for kind in tracked
    )


def observe_tracked_metrics(
    tracked: Sequence[str], *, task: EvalTask, trajectory: Trajectory
) -> dict[str, float]:
    """Measure every tracked metric for one sample — recorded, never scored.

    Deliberately NOT routed through ``score_checks``: that would return a
    composite, and the one guarantee an observation owes is that no composite
    exists to move. The result rides the sample ledger as a sibling field.
    """
    return {
        check.name: evaluate_check(check, task, trajectory).score
        for check in observation_checks(tracked)
    }
