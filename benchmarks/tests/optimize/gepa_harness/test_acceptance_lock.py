"""The adapter acceptance lock (ADR 0017 §8): acceptance consumes ONLY a
GateDecision + pre-registration config. Pinned like test_gate.py — a transitive
import walk + a signature test.
"""

from __future__ import annotations

import ast
import typing
from pathlib import Path

import pydocs_eval
import pytest

from pydocs_eval.optimize.gepa_harness import acceptance as acceptance_mod
from pydocs_eval.optimize.gepa_harness.acceptance import (
    AcceptanceConfig,
    decide_acceptance,
)
from pydocs_eval.trajectory.gate import GateDecision


def _decision(*, resolve_rate: float, cost_usd: float, within_budget: bool = True) -> GateDecision:
    return GateDecision(
        resolve_rate=resolve_rate,
        n_graded=10,
        n_infra_excluded=0,
        cost_usd=cost_usd,
        within_budget=within_budget,
        passed=within_budget,
    )


def test_accept_when_resolve_beats_seed_and_cost_within_threshold() -> None:
    cfg = AcceptanceConfig(c_sel=10.0, seed_resolve_rate=0.5)
    assert decide_acceptance(_decision(resolve_rate=0.6, cost_usd=8.0), cfg) is True


def test_reject_when_resolve_ties_seed() -> None:
    cfg = AcceptanceConfig(c_sel=10.0, seed_resolve_rate=0.5)
    assert decide_acceptance(_decision(resolve_rate=0.5, cost_usd=8.0), cfg) is False


def test_reject_when_cost_over_threshold() -> None:
    cfg = AcceptanceConfig(c_sel=10.0, seed_resolve_rate=0.5)
    assert decide_acceptance(_decision(resolve_rate=0.9, cost_usd=11.0), cfg) is False


def test_reject_when_over_budget() -> None:
    cfg = AcceptanceConfig(c_sel=100.0, seed_resolve_rate=0.5)
    assert (
        decide_acceptance(_decision(resolve_rate=0.9, cost_usd=8.0, within_budget=False), cfg)
        is False
    )


def test_config_rejects_out_of_range_seed_rate() -> None:
    with pytest.raises(ValueError, match="seed_resolve_rate must be in"):
        AcceptanceConfig(c_sel=1.0, seed_resolve_rate=1.5)


def test_config_rejects_negative_cost_threshold() -> None:
    with pytest.raises(ValueError, match="c_sel must be >= 0"):
        AcceptanceConfig(c_sel=-1.0, seed_resolve_rate=0.5)


# --------------------------------------------------------------------------
# The import + signature locks (mirror test_gate.py)
# --------------------------------------------------------------------------

_PKG_ROOT = Path(pydocs_eval.__file__).parent
_PKG_PREFIX = "pydocs_eval."
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
    # Non-vacuous: it reaches the gate (its sole input) and the gate's own graph.
    assert "pydocs_eval.trajectory.gate" in reachable
    assert "pydocs_eval.trajectory.eval_report" in reachable


def test_decide_acceptance_signature_takes_only_gate_and_config() -> None:
    """Feeding a shaped score is a type error: the only params are gate + config."""
    hints = typing.get_type_hints(decide_acceptance)
    assert hints["decision"] is GateDecision
    assert hints["config"] is AcceptanceConfig
    assert hints["return"] is bool
