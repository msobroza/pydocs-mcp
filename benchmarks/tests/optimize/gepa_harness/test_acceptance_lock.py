"""The adapter acceptance lock (ADR 0017 §8 amended; ADR 0018): acceptance is the
paired one-sided exact McNemar over ground-truth per-instance resolve + the two
gate decisions + the pre-registration config. Pinned like test_gate.py — a
transitive import walk + a signature test — plus a regression proving the paired
rule rejects a null candidate strict-improvement would accept.
"""

from __future__ import annotations

import ast
import typing
from collections.abc import Sequence
from pathlib import Path

import pydocs_eval
import pytest

from pydocs_eval.optimize.gepa_harness import acceptance as acceptance_mod
from pydocs_eval.optimize.gepa_harness.acceptance import (
    AcceptanceConfig,
    AcceptanceDecision,
    decide_acceptance,
)
from pydocs_eval.trajectory.eval_report import (
    GroundTruthOutcome,
    no_report_outcome,
    outcome_from_report,
)
from pydocs_eval.trajectory.gate import GateDecision


def _resolved(instance_id: str) -> GroundTruthOutcome:
    """A ground-truth outcome that strictly resolves (one gold F2P, all passing)."""
    report = {
        instance_id: {
            "tests_status": {
                "FAIL_TO_PASS": {"success": ["t"], "failure": []},
                "PASS_TO_PASS": {"success": [], "failure": []},
            },
            "patch_successfully_applied": True,
            "resolved": True,
        }
    }
    return outcome_from_report(instance_id, report, gold_f2p=["t"], gold_p2p=[])


def _seq(resolved_ids: set[str], all_ids: list[str]) -> list[GroundTruthOutcome]:
    return [_resolved(i) if i in resolved_ids else no_report_outcome(i) for i in all_ids]


def _gate(*, cost_usd: float = 1.0, within_budget: bool = True) -> GateDecision:
    return GateDecision(
        resolve_rate=0.0,
        n_graded=10,
        n_infra_excluded=0,
        cost_usd=cost_usd,
        within_budget=within_budget,
        passed=within_budget,
    )


_CFG = AcceptanceConfig(alpha=0.05, c_sel=10.0)


# --------------------------------------------------------------------------
# Regression: the paired rule rejects null candidates strict improvement accepts
# --------------------------------------------------------------------------


def test_null_effect_strict_improvement_would_accept_is_rejected() -> None:
    """b=2, c=1: candidate resolves +1 net (strict improvement accepts) but the
    one-sided exact p is 0.5 — REJECTED at alpha=0.05 (the anti-pattern ADR 0018
    buried)."""
    ids = [f"i{k}" for k in range(10)]
    incumbent = _seq({f"i{k}" for k in range(2, 10)}, ids)  # resolves i2..i9 (8)
    candidate = _seq({"i0", "i1", *(f"i{k}" for k in range(3, 10))}, ids)  # resolves 9, misses i2
    decision = decide_acceptance(incumbent, candidate, _gate(), _gate(), _CFG)
    assert (decision.b, decision.c) == (2, 1)
    assert decision.p_value == pytest.approx(0.5)
    assert decision.accepted is False


def test_strong_effect_is_accepted() -> None:
    """b=12, c=0: a decisive paired gain clears alpha and is accepted."""
    ids = [f"i{k}" for k in range(20)]
    incumbent = _seq({f"i{k}" for k in range(12, 20)}, ids)  # resolves i12..i19 (8)
    candidate = _seq(set(ids), ids)  # resolves all 20
    decision = decide_acceptance(incumbent, candidate, _gate(), _gate(), _CFG)
    assert (decision.b, decision.c) == (12, 0)
    assert decision.p_value < 0.05
    assert decision.accepted is True


def test_strong_effect_rejected_when_cost_over_c_sel() -> None:
    """A decisive effect is still rejected if the candidate blows the cost threshold."""
    ids = [f"i{k}" for k in range(20)]
    incumbent = _seq({f"i{k}" for k in range(12, 20)}, ids)
    candidate = _seq(set(ids), ids)
    over = _gate(cost_usd=11.0)  # > c_sel=10.0
    decision = decide_acceptance(incumbent, candidate, _gate(), over, _CFG)
    assert decision.cost_within_c_sel is False
    assert decision.accepted is False


def test_strong_effect_rejected_when_candidate_over_budget() -> None:
    """An over-budget candidate gate blocks acceptance regardless of the effect."""
    ids = [f"i{k}" for k in range(20)]
    incumbent = _seq({f"i{k}" for k in range(12, 20)}, ids)
    candidate = _seq(set(ids), ids)
    decision = decide_acceptance(incumbent, candidate, _gate(), _gate(within_budget=False), _CFG)
    assert decision.accepted is False


def test_candidate_worse_gives_p_above_half() -> None:
    """b < c (candidate resolves fewer) yields p > 0.5 — never significant."""
    ids = [f"i{k}" for k in range(10)]
    incumbent = _seq(set(ids), ids)  # resolves all
    candidate = _seq({f"i{k}" for k in range(3, 10)}, ids)  # misses i0..i2 (c=3, b=0)
    decision = decide_acceptance(incumbent, candidate, _gate(), _gate(), _CFG)
    assert (decision.b, decision.c) == (0, 3)
    assert decision.p_value > 0.5
    assert decision.accepted is False


def test_key_set_mismatch_is_a_hard_error() -> None:
    """A paired test on differing instance lists is a campaign bug, not data."""
    incumbent = _seq(set(), ["i0", "i1"])
    candidate = _seq(set(), ["i0", "i2"])
    with pytest.raises(ValueError, match="key sets differ"):
        decide_acceptance(incumbent, candidate, _gate(), _gate(), _CFG)


def test_config_rejects_out_of_range_alpha() -> None:
    with pytest.raises(ValueError, match="alpha must be in"):
        AcceptanceConfig(alpha=1.5, c_sel=1.0)


def test_config_rejects_negative_cost_threshold() -> None:
    with pytest.raises(ValueError, match="c_sel must be >= 0"):
        AcceptanceConfig(alpha=0.05, c_sel=-1.0)


# --------------------------------------------------------------------------
# The import + signature locks (mirror test_gate.py)
# --------------------------------------------------------------------------

_PKG_ROOT = Path(pydocs_eval.__file__).parent
_PKG_PREFIX = "pydocs_eval."
# ``metrics`` here is ``pydocs_eval.trajectory.metrics`` (the shaped-score
# module) — NOT ``pydocs_eval.metrics.aggregate`` (the retrieval-metrics module
# carrying the exact-test helper), which the closure is ALLOWED to reach.
_FORBIDDEN = {
    "pydocs_eval.trajectory.shaped_score",
    "pydocs_eval.trajectory.metrics",
    "pydocs_eval.trajectory.consumers",
    "pydocs_eval.trajectory.feedback",
    "pydocs_eval.trajectory.attribution",
}


def _module_source(dotted: str) -> Path | None:
    rel = dotted.removeprefix(_PKG_PREFIX).replace(".", "/")
    for candidate in (_PKG_ROOT / f"{rel}.py", _PKG_ROOT / rel / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _pydocs_imports_in(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return {name for name in names if name.startswith(_PKG_PREFIX)}


def _reachable(root: Path) -> set[str]:
    seen: set[str] = set()
    frontier = _pydocs_imports_in(root)
    while frontier:
        module = frontier.pop()
        if module in seen:
            continue
        seen.add(module)
        source = _module_source(module)
        if source is not None:
            frontier |= _pydocs_imports_in(source) - seen
    return seen


def test_acceptance_transitive_imports_exclude_scoring() -> None:
    """The acceptance module's whole closure never reaches a shaped-score module."""
    reachable = _reachable(Path(acceptance_mod.__file__))
    assert reachable.isdisjoint(_FORBIDDEN), reachable & _FORBIDDEN
    # Non-vacuous: it reaches the two ground-truth gate inputs + the exact-test
    # helper's retrieval-metrics module (distinct from the forbidden metrics).
    assert "pydocs_eval.trajectory.gate" in reachable
    assert "pydocs_eval.trajectory.eval_report" in reachable
    assert "pydocs_eval.metrics.aggregate" in reachable


def test_decide_acceptance_signature_takes_only_ground_truth_gate_and_config() -> None:
    """Feeding a shaped score is a type error: params are outcomes + gates + config."""
    hints = typing.get_type_hints(decide_acceptance)
    assert hints["incumbent"] == Sequence[GroundTruthOutcome]
    assert hints["candidate"] == Sequence[GroundTruthOutcome]
    assert hints["incumbent_gate"] is GateDecision
    assert hints["candidate_gate"] is GateDecision
    assert hints["config"] is AcceptanceConfig
    assert hints["return"] is AcceptanceDecision
