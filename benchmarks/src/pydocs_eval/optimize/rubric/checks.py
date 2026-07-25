"""Scored deterministic checks — the 0-1 generalization of ``gates.py``.

A gate answers *did it pass*; a check answers *how well did it do*, and may
ALSO gate. That extra bit is what a recall metric needs: "named 3 of the 5 gold
files" is real signal, and thresholding it into a boolean throws the gradient
away right where the optimizers want it (SkillOpt's ``soft``, GEPA's per-instance
``Feedback``).

The policy fields are named to match ``coding-agent-playbook``'s
``Metric``/``Check``/``Rubric`` model, so the two engines stay legible side by
side and a convention learned in one transfers:

* ``weight`` — the check's share of the deterministic composite;
* ``required`` — a failed required check trips ``fail_fast`` and spares the judge
  (the free-screen property of today's gates, preserved exactly);
* ``fail`` — the 0-1 cutoff below which the check counts as failed; ``None``
  means it scores but never fails, hence never blocks.

Those three compose the two roles a gate cannot separate today:

===========================  =======  =======  ==========================
config                       gates?   scores?  role
===========================  =======  =======  ==========================
``weight=0, required=True``  yes      no       pure screen (today's gate)
``weight>0, required=True``  yes      yes      screen + partial credit
``weight>0, fail=None``      no       yes      pure measure (recall)
===========================  =======  =======  ==========================

**Multi-task.** ``applies_to`` restricts a check to some task types — a CVE check
is meaningless for a swe-qa-pro row, and letting it "pass vacuously" would inflate
that row's score with a free 1.0. ``weight_by_type`` re-weights per type. The
composite renormalizes over APPLICABLE weights only, so a task graded on five
checks stays comparable to one graded on three.

**Back-compat.** ``evaluate_check`` resolves a kind in ``check_registry`` first and
falls back to ``gates.gate_registry``, wrapping the boolean as ``1.0``/``0.0``. So
every gate kind already registered is usable as a check with no porting, and an
unmigrated ``gates:`` config keeps its exact verdicts (weight 1.0, required, fail
1.0 reproduces ``fmean`` over booleans).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.optimize._prefix_report import task_id_prefix
from pydocs_eval.optimize.rubric.gates import (
    TranscriptLike,
    _all_gate_candidates,
    gate_registry,
)
from pydocs_eval.registries import _Registry

__all__ = [
    "Check",
    "CheckOutcome",
    "CheckScoring",
    "check_registry",
    "evaluate_check",
    "score_checks",
    "validate_checks",
]


class CheckPredicate(Protocol):
    """A pure per-sample 0-1 measurement (the scored sibling of GatePredicate)."""

    def __call__(
        self, task: EvalTask, transcript: TranscriptLike, params: Mapping[str, object]
    ) -> float: ...


check_registry: _Registry[CheckPredicate] = _Registry()


@dataclass(frozen=True, slots=True)
class Check:
    """One deterministic 0-1 measurement that may also gate.

    See the module docstring for the ``weight``/``required``/``fail`` role table
    and the multi-task fields.
    """

    name: str
    kind: str
    params: Mapping[str, object] = field(default_factory=dict)
    weight: float = 1.0
    required: bool = True
    fail: float | None = 1.0
    #: Task types this check applies to; empty means every type.
    applies_to: tuple[str, ...] = ()
    #: Per-task-type weight overrides; falls back to ``weight``.
    weight_by_type: Mapping[str, float] = field(default_factory=dict)

    def applicable(self, task_type: str) -> bool:
        """Whether this check is meaningful for ``task_type`` (empty = all)."""
        return not self.applies_to or task_type in self.applies_to

    def weight_for(self, task_type: str) -> float:
        """This check's weight for ``task_type``, defaulting to ``weight``."""
        return self.weight_by_type.get(task_type, self.weight)


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """One check's graded result."""

    score: float
    passed: bool
    #: ``required and not passed`` — the only thing that can spare the judge.
    blocking: bool


@dataclass(frozen=True, slots=True)
class CheckScoring:
    """Every applicable check's outcome plus the renormalized composite."""

    outcomes: Mapping[str, CheckOutcome]
    score: float
    blocked: bool


def evaluate_check(check: Check, task: EvalTask, transcript: TranscriptLike) -> CheckOutcome:
    """Run ``check`` and decide ``passed``/``blocking`` from its policy.

    Raises:
        ValueError: the predicate returned a value outside ``0-1``, naming the
            check, its kind, and the offending value.
        KeyError: ``check.kind`` is in neither registry, naming both.
    """
    score = _predicate(check.kind)(task, transcript, check.params)
    if not 0.0 <= score <= 1.0:
        raise ValueError(
            f"check {check.name!r} (kind {check.kind!r}) returned {score!r}; expected a 0-1 score"
        )
    passed = True if check.fail is None else score >= check.fail
    return CheckOutcome(score=score, passed=passed, blocking=check.required and not passed)


def score_checks(
    checks: Sequence[Check], task: EvalTask, transcript: TranscriptLike
) -> CheckScoring:
    """Grade every check APPLICABLE to ``task``, renormalized over their weights.

    Non-applicable checks are excluded entirely — they neither score nor block,
    so a CVE check cannot sink (or inflate) an unrelated swe-qa-pro row.
    Renormalizing over the applicable weight sum is what keeps task types with
    different check sets comparable on one 0-1 scale.
    """
    _require_unique_names(checks)
    task_type = task_id_prefix(task.task_id)
    applicable = [c for c in checks if c.applicable(task_type)]
    outcomes = {c.name: evaluate_check(c, task, transcript) for c in applicable}

    weights = {c.name: c.weight_for(task_type) for c in applicable}
    total = sum(weights.values())
    score = (
        sum(weights[name] * outcome.score for name, outcome in outcomes.items()) / total
        if total
        else 1.0
    )
    return CheckScoring(
        outcomes=outcomes,
        score=score,
        blocked=any(outcome.blocking for outcome in outcomes.values()),
    )


def validate_checks(checks: Sequence[Check], *, known_task_types: Sequence[str]) -> None:
    """Fail loud on a multi-task config that cannot do what it looks like it does.

    Two failure modes are silent without this and expensive with it: a typo in
    ``applies_to`` disables a check rather than erroring, and a task type with no
    required applicable check can never short-circuit ``fail_fast`` — so every
    sample of that type pays the judge, quietly, forever.

    Raises:
        ValueError: an unknown task type in ``applies_to`` / ``weight_by_type``,
            a task type with no required applicable check, or one whose
            applicable checks all carry zero weight.
    """
    _require_unique_names(checks)
    known = set(known_task_types)
    for check in checks:
        # A "required" check that cannot fail is a no-op wearing a gate's name.
        # `passed` is unconditionally True when `fail is None`, and `score >= 0.0`
        # holds for every 0-1 score — so both spellings would certify a config
        # whose judge-avoidance guarantee silently does not exist.
        if check.required and (check.fail is None or check.fail <= 0.0):
            raise ValueError(
                f"check {check.name!r} is required but can never fail "
                f"(fail={check.fail!r}); a required check needs a cutoff above 0, "
                "otherwise it blocks nothing and the judge is always paid"
            )
        for field_name, names in (
            ("applies_to", tuple(check.applies_to)),
            ("weight_by_type", tuple(check.weight_by_type)),
        ):
            unknown = sorted(set(names) - known)
            if unknown:
                raise ValueError(
                    f"check {check.name!r} {field_name} names unknown task type(s) "
                    f"{unknown}; known types are {sorted(known)} — a typo here would "
                    "silently disable the check instead of failing"
                )

    for task_type in sorted(known):
        applicable = [c for c in checks if c.applicable(task_type)]
        if not any(c.required for c in applicable):
            raise ValueError(
                f"task type {task_type!r} has no required applicable check: fail_fast "
                "can never short-circuit it, so EVERY sample of this type pays the judge"
            )
        if not any(c.weight_for(task_type) > 0 for c in applicable):
            raise ValueError(
                f"task type {task_type!r} has no applicable check with weight > 0, so "
                "its deterministic composite carries no signal"
            )


def _require_unique_names(checks: Sequence[Check]) -> None:
    """Reject duplicate check names, mirroring the gate config's own rule.

    Outcomes are keyed by name, so a duplicate would silently drop a check —
    including a required screen, which would take the judge-avoidance guarantee
    with it. ``model._require_unique_gate_names`` enforces the same rule for
    gates at config-load time; this is its sibling.
    """
    names = [c.name for c in checks]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(
            f"check names must be unique; duplicated: {duplicates} — outcomes are "
            "keyed by name, so a duplicate silently drops one of the checks"
        )


def _predicate(kind: str) -> CheckPredicate:
    """Resolve ``kind`` as a check, else adapt the boolean gate of that name.

    The fallback is what makes every already-registered gate usable as a check
    with no porting: its boolean becomes ``1.0``/``0.0``.
    """
    if kind in check_registry.names():
        return check_registry.build(kind)
    if kind in gate_registry.names():
        gate = gate_registry.build(kind)

        def as_score(
            task: EvalTask, transcript: TranscriptLike, params: Mapping[str, object]
        ) -> float:
            return float(gate(task, transcript, params))

        return as_score
    raise KeyError(
        f"unknown check kind {kind!r}; checks are {sorted(check_registry.names())} "
        f"and boolean gates are {sorted(gate_registry.names())}"
    )


@check_registry.register("gold_recall")
@dataclass(frozen=True, slots=True)
class GoldRecall:
    """Fraction of the gold candidates named verbatim in the answer.

    The scored generalization of ``gold_substring`` (ANY) and
    ``gold_substring_all`` (ALL): those are this metric with ``fail`` just above
    ``0`` and ``fail=1.0`` respectively. ``params["keys"]`` reuses the gate
    candidate filter (``file_set``, ``cve_id``, ``cwe_id_0``…). No candidates
    scores ``1.0``, mirroring the boolean siblings' vacuous pass.
    """

    def __call__(
        self, task: EvalTask, transcript: TranscriptLike, params: Mapping[str, object]
    ) -> float:
        candidates = _all_gate_candidates(task, params.get("keys"))
        if not candidates:
            return 1.0
        return sum(1 for c in candidates if c in transcript.answer) / len(candidates)


@check_registry.register("cve_id_exact")
@dataclass(frozen=True, slots=True)
class CveIdExact:
    """The record's CVE id appears verbatim — exact identification, not a class."""

    def __call__(
        self, task: EvalTask, transcript: TranscriptLike, params: Mapping[str, object]
    ) -> float:
        _ = params
        cve = task.gold.extra.get("cve_id")
        return float(isinstance(cve, str) and bool(cve) and cve in transcript.answer)
