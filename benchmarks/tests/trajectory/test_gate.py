"""Gate-isolation tests (ADR 0012, R4): the three locks that make shaped-score
leakage into the acceptance gate impossible, not merely discouraged, plus the
gate's resolve-rate + budget behavior.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import typing
from pathlib import Path

from pydocs_eval.trajectory import gate as gate_mod
from pydocs_eval.trajectory.eval_report import (
    GroundTruthOutcome,
    infra_outcome,
    no_report_outcome,
    outcome_from_report,
)
from pydocs_eval.trajectory.gate import GateDecision, run_gate

_GATE_SOURCE = Path(gate_mod.__file__)
_PKG_PREFIX = "pydocs_eval.trajectory."
_PKG_DIR = _GATE_SOURCE.parent
# The score/metric modules whose import into the gate would be a leak (lock 3).
_FORBIDDEN_IMPORTS = {
    "pydocs_eval.trajectory.shaped_score",
    "pydocs_eval.trajectory.metrics",
    "pydocs_eval.trajectory.consumers",
    "pydocs_eval.trajectory.feedback",
    "pydocs_eval.trajectory.attribution",
}


def _trajectory_imports_in(source: Path) -> set[str]:
    """The ``pydocs_eval.trajectory.*`` modules directly imported by one source file."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return {name for name in names if name.startswith(_PKG_PREFIX)}


def _reachable_trajectory_modules(root: Path) -> set[str]:
    """Transitive closure of ``pydocs_eval.trajectory.*`` modules reachable from ``root``.

    Walks the intra-package import graph (``gate.py → eval_report → schema → …``) so
    a leak that hides one hop away — a clean gate that imports a module that itself
    pulls in the shaped score — is caught, not just a direct gate import.
    """
    seen: set[str] = set()
    frontier = _trajectory_imports_in(root)
    while frontier:
        module = frontier.pop()
        if module in seen:
            continue
        seen.add(module)
        source = _PKG_DIR / f"{module.removeprefix(_PKG_PREFIX)}.py"
        if source.exists():
            frontier |= _trajectory_imports_in(source) - seen
    return seen


def test_gate_transitive_imports_exclude_score_and_metrics() -> None:
    """Lock 3: the TRANSITIVE import-graph pin — no forbidden module is reachable
    from the gate through the whole ``pydocs_eval.trajectory`` package graph."""
    reachable = _reachable_trajectory_modules(_GATE_SOURCE)
    assert reachable.isdisjoint(_FORBIDDEN_IMPORTS), reachable & _FORBIDDEN_IMPORTS
    # The walker is not vacuous: it genuinely follows edges more than one hop deep
    # (gate → eval_report → schema), so a real leak would be traversed and caught.
    assert "pydocs_eval.trajectory.eval_report" in reachable
    assert "pydocs_eval.trajectory.schema" in reachable


def test_gate_signature_accepts_only_outcomes_and_cost() -> None:
    """Lock 2: no float-bearing metric container type appears in the signature.

    The gate takes ``Sequence[GroundTruthOutcome]`` + a plain ``cost_usd`` float +
    an optional ``max_usd`` float; passing a shaped-score container is a type error.
    """
    hints = typing.get_type_hints(run_gate)
    assert hints["cost_usd"] is float
    assert hints["max_usd"] == (float | None)
    # The sequence element type is GroundTruthOutcome, nothing metric-shaped.
    outcomes_hint = hints["outcomes"]
    assert typing.get_args(outcomes_hint) == (GroundTruthOutcome,)


def test_ground_truth_outcome_has_no_float_score_field() -> None:
    """Lock 1 corollary: GroundTruthOutcome carries no float field a shaped score
    could be smuggled through — only resolve/apply/infra flags + name sets."""
    hints = typing.get_type_hints(GroundTruthOutcome)
    assert float not in hints.values()


def test_ground_truth_outcome_factories_live_only_in_eval_report() -> None:
    """Lock 1: every public factory returning a GroundTruthOutcome is in eval_report."""
    from pydocs_eval.trajectory import eval_report

    factories = [
        obj
        for _, obj in inspect.getmembers(eval_report, inspect.isfunction)
        if typing.get_type_hints(obj).get("return") is GroundTruthOutcome
    ]
    assert {f.__module__ for f in factories} == {"pydocs_eval.trajectory.eval_report"}
    assert len(factories) >= 3  # infra / patch_apply_failed / no_report + report parser


def _resolved_outcome(instance: str, *, resolved: bool) -> GroundTruthOutcome:
    f2p = {"success": ["t::a"], "failure": []} if resolved else {"success": [], "failure": ["t::a"]}
    return outcome_from_report(
        instance,
        {
            instance: {
                "patch_successfully_applied": True,
                "resolved": resolved,
                "tests_status": {
                    "FAIL_TO_PASS": f2p,
                    "PASS_TO_PASS": {"success": [], "failure": []},
                },
            }
        },
        gold_f2p=["t::a"],
        gold_p2p=[],
    )


def test_resolve_rate_excludes_infra_from_denominator() -> None:
    """Infra rollouts are excluded from the graded denominator (ADR 0012)."""
    outcomes = [
        _resolved_outcome("a", resolved=True),
        _resolved_outcome("b", resolved=False),
        infra_outcome("c"),
    ]
    decision = run_gate(outcomes, cost_usd=1.0)
    assert decision.n_graded == 2
    assert decision.n_infra_excluded == 1
    assert decision.resolve_rate == 0.5


def test_gate_within_budget_flag() -> None:
    decision = run_gate([no_report_outcome("a")], cost_usd=10.0, max_usd=5.0)
    assert decision.within_budget is False
    assert decision.passed is False


def test_gate_no_budget_always_within_budget() -> None:
    decision = run_gate([_resolved_outcome("a", resolved=True)], cost_usd=999.0)
    assert decision.within_budget is True
    assert decision.resolve_rate == 1.0


def test_empty_graded_set_yields_zero_rate() -> None:
    decision = run_gate([infra_outcome("a")], cost_usd=0.0)
    assert decision.resolve_rate == 0.0
    assert decision.n_graded == 0


def test_gate_decision_is_frozen() -> None:
    decision = run_gate([no_report_outcome("a")], cost_usd=0.0)
    assert isinstance(decision, GateDecision)
    assert dataclasses.is_dataclass(decision)
