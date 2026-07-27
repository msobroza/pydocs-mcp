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
    """The whole configurable objective: gates + criteria + layer weights."""

    gates: tuple[GateCheck, ...]
    criteria: tuple[RubricCriterion, ...]
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
        "criteria": [
            {"name": c.name, "weight": c.weight, "description": c.description}
            for c in config.criteria
        ],
    }
    rendered = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def validate_rubric_config(config: RubricConfig, *, registered_gate_kinds: Sequence[str]) -> None:
    """Fail-loud config validation, called at run-config load time (spec §3.4.1).

    Raises:
        ValueError: weights off by more than ``_WEIGHT_TOLERANCE``, duplicate
            gate names, or an empty gates+criteria config — each named with
            the offending values.
        KeyError: a gate ``kind`` outside ``registered_gate_kinds``, naming
            the registered kinds (the AC-7 contract).
    """
    if not config.gates and not config.criteria:
        raise ValueError("rubric config must carry at least one of gates/criteria")
    _require_unique_gate_names(config.gates)
    _require_registered_gate_kinds(config.gates, registered_gate_kinds)
    _require_unique_criterion_names(config.criteria)
    if config.criteria:
        total = sum(c.weight for c in config.criteria)
        if not math.isclose(total, 1.0, abs_tol=_WEIGHT_TOLERANCE):
            raise ValueError(
                f"criterion weights must sum to 1.0 ± {_WEIGHT_TOLERANCE}; "
                f"got {total} from {[c.weight for c in config.criteria]}"
            )
    elif not math.isclose(config.rubric_weight, 0.0, abs_tol=_WEIGHT_TOLERANCE):
        # A gates-only objective with rubric_weight > 0 silently caps every
        # verdict at gate_weight — a config error, not a tuning choice.
        raise ValueError(
            f"gates-only config must set rubric_weight to 0.0 (and gate_weight "
            f"to 1.0); got rubric_weight={config.rubric_weight}"
        )
    layer_total = config.gate_weight + config.rubric_weight
    if not math.isclose(layer_total, 1.0, abs_tol=_WEIGHT_TOLERANCE):
        raise ValueError(
            f"gate_weight + rubric_weight must sum to 1.0 ± {_WEIGHT_TOLERANCE}; "
            f"got {config.gate_weight} + {config.rubric_weight} = {layer_total}"
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
