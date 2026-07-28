"""Rubric data model — gates, criteria, config, and the objective identity (spec §3.4.1).

The layering follows the gate → rubric → verdict task model: deterministic
boolean ``GateCheck``s screen for free, weighted judged ``RubricCriterion``s
score what survives, and the weighted composite verdict ranks candidates on
the ladder. ``rubric_config_hash`` is the objective identity that keys both
ledgers — a config edit, a re-pinned runner architecture, or a changed
execution path (``binding_identity``) can never falsely resume samples scored
against a different objective (spec §3.6, run-contract design §8).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # WHY TYPE_CHECKING only: ``checks`` imports ``gates``, which imports
    # ``GateCheck`` from THIS module — a runtime import here would close that
    # cycle. Nothing below needs the type at runtime: the hash reads attributes
    # and the validators only compare names, so the coupling is purely a typing
    # one (and ``from __future__ import annotations`` keeps the field
    # annotation a string).
    from pydocs_eval.optimize.rubric.checks import Check

# Single sources for the layer defaults (§"Default values"): the run-config
# pydantic fields and the shipped YAMLs restate these for user clarity.
_DEFAULT_FAIL_FAST = True
_DEFAULT_GATE_WEIGHT = 0.3
_DEFAULT_RUBRIC_WEIGHT = 0.7
# False keeps every objective minted before the knob existed byte-identical.
_DEFAULT_KEEP_DETERMINISTIC_ON_SKIP = False
# WHY 1e-3: weights are human-authored YAML floats; the tolerance admits
# rounding like 0.3333*3 while still catching a genuinely wrong 0.98 sum.
_WEIGHT_TOLERANCE = 1e-3


@dataclass(frozen=True, slots=True)
class GateCheck:
    """Deterministic, free, per-sample boolean predicate (spec §3.4.2).

    ``kind`` keys into ``gate_registry``; ``params`` are the predicate's
    knobs (e.g. ``{"n": 40}``). ``name`` is the unique label ledger lines and
    report components use.
    """

    name: str
    kind: str
    params: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RubricCriterion:
    """One judged 0-10 dimension with a weight (spec §3.4.1).

    ``description`` is inserted verbatim into the judge prompt as the scoring
    guidance for this dimension.
    """

    name: str
    weight: float
    description: str


@dataclass(frozen=True, slots=True)
class RubricConfig:
    """The whole configurable objective: gates + checks + criteria + layer weights."""

    gates: tuple[GateCheck, ...]
    criteria: tuple[RubricCriterion, ...]
    #: SCORED deterministic measures (``rubric/checks.py``) sharing the
    #: deterministic layer with ``gates``. Empty — the default — leaves the
    #: layer exactly what it always was: the pass fraction over the boolean
    #: gates. Non-empty flips the gates to the PURE SCREEN role they were
    #: always described as (weight 0, still required, still fail_fast) and
    #: hands the layer's whole mass to these weighted 0-1 measures. See
    #: ``checks.deterministic_checks``, which is the one place that rule lives.
    checks: tuple[Check, ...] = ()
    fail_fast: bool = _DEFAULT_FAIL_FAST
    gate_weight: float = _DEFAULT_GATE_WEIGHT
    rubric_weight: float = _DEFAULT_RUBRIC_WEIGHT
    #: When fail_fast skips the judge, keep the deterministic layer's score
    #: instead of zeroing the verdict. The cliff was defensible while gates were
    #: pure screens; once the deterministic layer carries a graded score it
    #: discards real measurement the harness already paid for. Default False
    #: keeps the objective byte-identical. Configurable per objective section
    #: through ``AskRubricSettings.keep_deterministic_on_skip``.
    keep_deterministic_on_skip: bool = _DEFAULT_KEEP_DETERMINISTIC_ON_SKIP


@dataclass(frozen=True, slots=True)
class SampleRubricRecord:
    """One sample's full scoring outcome — the sample-ledger line (spec §3.4.5).

    ``answer_sha256`` (not the raw answer) keeps the ledger small and
    non-sensitive; the full trajectory lives in the per-sample file. A
    ``discarded`` reason means the sample is excluded from the fitness score,
    never admitted partially scored.

    ``tracked`` carries an arm's OBSERVATIONAL metrics (the ``scoring.tracked``
    cell key) as a defaulted sibling field — the ``.get``-tolerant pattern, so
    a line written before the field existed still parses and no already-paid
    ledger is orphaned. Nothing here feeds ``verdict``.

    ``arm_hash`` is that same sibling-field pattern applied to WHICH ARM
    produced the line (run-contract design §6), and unlike ``tracked`` it IS
    part of the resume key: two arms of one run legitimately share a candidate
    fingerprint, a split, a task id AND an objective while measuring different
    things — the shipped ``arms:`` pair differs only in ``tool_names``. The
    empty default is the single implicit arm every pre-``arms:`` line belongs
    to, so a legacy row can never match a real 64-hex arm hash.

    ``record_id`` applies the same sibling-field pattern to the RECORD the row
    was minted from (run-contract design §5). It is deliberately NOT in the
    resume key — the task id already identifies the row — it is what makes the
    paired statistics' record-level clustering (platform spec §5.4) computable
    from a ledger alone now that one record can carry two framings. Empty means
    "the task id IS the record", which is every pre-framing line.
    """

    fingerprint: str
    split: str
    task_id: str
    qa_type: str
    objective_hash: str
    gates: Mapping[str, bool]
    gate_pass_fraction: float
    judge_skipped: bool
    criteria: Mapping[str, float]
    rubric_score: float
    verdict: float
    turns: int
    wall_seconds: float
    cost_usd: float
    answer_sha256: str
    discarded: str | None = None
    tracked: Mapping[str, float] = field(default_factory=dict)
    arm_hash: str = ""
    record_id: str = ""
    #: The SCORED deterministic measures' per-sample values — the
    #: ``RubricConfig.checks`` half of ``gate_pass_fraction``. Same defaulted
    #: sibling-field pattern as ``tracked``, and unlike ``tracked`` these DO
    #: move the verdict, which is why the composite they feed is already
    #: recorded in ``gate_pass_fraction``; this field is what makes it
    #: decomposable after the fact. Empty for a gates-only objective, and the
    #: line drops the key entirely then, so no pre-checks ledger byte moves.
    checks: Mapping[str, float] = field(default_factory=dict)


def rubric_config_hash(
    config: RubricConfig,
    *,
    architecture: str,
    binding_identity: Mapping[str, str] | None = None,
) -> str:
    """sha256 of the canonical config JSON + the runner architecture + binding identity.

    The objective identity (spec §3.6): which graph answered is part of the
    measurement, so the pinned architecture folds in — re-pinning a campaign
    can never falsely resume samples scored under a different graph.

    ``binding_identity`` (run-contract design §8) is the same rule applied to
    the EXECUTION PATH: the task scaffold a sample was rendered with, the
    harness's section→channel delivery map, and which observation point the
    gates read all move recorded verdicts without touching a single rubric
    field. Folding one sorted-key mapping in makes those a versioned objective
    change instead of a silent re-scoring. Callers that observe none of it
    (a plain gates-only objective) pass ``None`` — the key is folded either
    way, so no hash minted before this input existed can collide with one
    minted after it.

    Example:
        >>> cfg = RubricConfig(gates=(), criteria=(RubricCriterion("c", 1.0, "d"),))
        >>> len(rubric_config_hash(cfg, architecture="text_react"))
        64
    """
    canonical = {
        "architecture": architecture,
        "binding_identity": dict(sorted(binding_identity.items())) if binding_identity else None,
        "fail_fast": config.fail_fast,
        "gate_weight": config.gate_weight,
        "rubric_weight": config.rubric_weight,
        "keep_deterministic_on_skip": config.keep_deterministic_on_skip,
        "gates": [
            {"name": g.name, "kind": g.kind, "params": dict(sorted(g.params.items()))}
            for g in config.gates
        ],
        # Folded UNCONDITIONALLY (the ``binding_identity`` rule): a weight here
        # moves every verdict, so an objective scored with checks must never be
        # able to collide with one scored without them. The key's arrival moves
        # every rubric hash exactly once, by design.
        "checks": [
            {
                "name": c.name,
                "kind": c.kind,
                "params": dict(sorted(c.params.items())),
                "weight": c.weight,
                "required": c.required,
                "fail": c.fail,
                "applies_to": list(c.applies_to),
                "weight_by_type": dict(sorted(c.weight_by_type.items())),
            }
            for c in config.checks
        ],
        "criteria": [
            {"name": c.name, "weight": c.weight, "description": c.description}
            for c in config.criteria
        ],
    }
    rendered = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def validate_rubric_config(
    config: RubricConfig,
    *,
    registered_gate_kinds: Sequence[str],
    registered_check_kinds: Sequence[str] = (),
) -> None:
    """Fail-loud config validation, called at run-config load time (spec §3.4.1).

    ``registered_check_kinds`` defaults to empty because a gates-only objective
    configures no checks; a config that DOES spell ``checks:`` must be validated
    against the check vocabulary, which only the caller can supply without
    closing the ``model`` → ``checks`` → ``gates`` → ``model`` import cycle.

    Raises:
        ValueError: weights off by more than ``_WEIGHT_TOLERANCE``, duplicate
            gate/check names, a checks block carrying no weight, or an empty
            gates+checks+criteria config — each named with the offending values.
        KeyError: a gate ``kind`` outside ``registered_gate_kinds`` or a check
            ``kind`` outside ``registered_check_kinds``, naming the registered
            kinds (the AC-7 contract).
    """
    if not config.gates and not config.checks and not config.criteria:
        raise ValueError("rubric config must carry at least one of gates/checks/criteria")
    _require_unique_gate_names(config.gates)
    _require_registered_gate_kinds(config.gates, registered_gate_kinds)
    _require_scoring_checks(config, registered_check_kinds)
    _require_unique_criterion_names(config.criteria)
    if config.criteria:
        total = sum(c.weight for c in config.criteria)
        if not math.isclose(total, 1.0, abs_tol=_WEIGHT_TOLERANCE):
            raise ValueError(
                f"criterion weights must sum to 1.0 ± {_WEIGHT_TOLERANCE}; "
                f"got {total} from {[c.weight for c in config.criteria]}"
            )
    elif not math.isclose(config.rubric_weight, 0.0, abs_tol=_WEIGHT_TOLERANCE):
        # A judge-free objective with rubric_weight > 0 silently caps every
        # verdict at gate_weight — a config error, not a tuning choice.
        raise ValueError(
            f"a deterministic-only config (no criteria) must set rubric_weight to 0.0 "
            f"(and gate_weight to 1.0); got rubric_weight={config.rubric_weight}"
        )
    layer_total = config.gate_weight + config.rubric_weight
    if not math.isclose(layer_total, 1.0, abs_tol=_WEIGHT_TOLERANCE):
        raise ValueError(
            f"gate_weight + rubric_weight must sum to 1.0 ± {_WEIGHT_TOLERANCE}; "
            f"got {config.gate_weight} + {config.rubric_weight} = {layer_total}"
        )


def _require_scoring_checks(config: RubricConfig, registered: Sequence[str]) -> None:
    """Validate the ``checks:`` block: unique names, known kinds, real mass.

    The weight guard is the load-time firewall for the silent failure this
    block introduces: with checks present the gates fall back to pure screens,
    so an all-zero-weight checks block leaves the deterministic layer scoring a
    vacuous 1.0 for EVERY sample — a config that looks tuned and measures
    nothing. A pure OBSERVATION belongs in an arm's ``tracked:`` list, which is
    exactly where weight 0 is the point.

    Raises:
        ValueError: a name collides with a gate/another check, or no check
            carries weight.
        KeyError: a ``kind`` outside ``registered``.
    """
    if not config.checks:
        return
    names = [g.name for g in config.gates] + [c.name for c in config.checks]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(
            f"gate and check names share one namespace and must be unique; duplicated: "
            f"{duplicates} — the deterministic layer keys outcomes by name, so a "
            "duplicate silently drops one measurement"
        )
    for check in config.checks:
        if check.kind not in registered:
            raise KeyError(
                f"unknown check kind {check.kind!r} on check {check.name!r}; "
                f"have {sorted(registered)}"
            )
        # Checked for EVERY check before the weight-mass test below, which
        # short-circuits on the first positive weight and would sail past a
        # later non-numeric one — straight into a TypeError inside the
        # composite, at trial 14, with the rollout already paid for.
        if isinstance(check.weight, bool) or not isinstance(check.weight, (int, float)):
            raise ValueError(f"check {check.name!r} weight must be a number; got {check.weight!r}")
    if not any(c.weight > 0 for c in config.checks):
        raise ValueError(
            f"the checks block carries no weight (weights {[c.weight for c in config.checks]}); "
            "with checks configured the gates become pure screens, so an all-zero block "
            "scores every sample a vacuous 1.0 — put pure observations in an arm's "
            "scoring.tracked instead"
        )


def _require_unique_criterion_names(criteria: tuple[RubricCriterion, ...]) -> None:
    names = [c.name for c in criteria]
    if len(names) != len(set(names)):
        duplicates = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"criterion names must be unique; duplicated: {duplicates}")


def _require_unique_gate_names(gates: tuple[GateCheck, ...]) -> None:
    names = [g.name for g in gates]
    if len(names) != len(set(names)):
        duplicates = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"gate names must be unique; duplicated: {duplicates}")


def _require_registered_gate_kinds(gates: tuple[GateCheck, ...], registered: Sequence[str]) -> None:
    for gate in gates:
        if gate.kind not in registered:
            raise KeyError(
                f"unknown gate kind {gate.kind!r} on gate {gate.name!r}; have {sorted(registered)}"
            )
