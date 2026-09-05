"""Final selection over the candidate ledger (ADR 0020 §Selection rule): computed
single-best within c_sel + the Pareto set, from GateDecision fields ONLY."""

from __future__ import annotations

import ast
from pathlib import Path

import pydocs_eval
import pytest

from pydocs_eval.optimize import selection as selection_mod
from pydocs_eval.optimize.candidates.ledger import (
    CandidateRecord,
    GateOutcome,
    MutationRecord,
)
from pydocs_eval.optimize.selection import select_final


def _record(
    name: str, resolve: float, cost: float, *, minibatch: dict[str, float] | None = None
) -> CandidateRecord:
    return CandidateRecord(
        candidate_hash=name,
        document_ref="ref",
        lineage_parent=None,
        mutation_record=MutationRecord(proposer="p"),
        reflector_input_refs=(),
        valid=True,
        violations=(),
        n_rollouts=1,
        minibatch_scores=minibatch or {},
        gate=GateOutcome(
            resolve_rate=resolve,
            n_graded=100,
            n_infra_excluded=0,
            cost_usd=cost,
            within_budget=True,
            passed=True,
        ),
    )


def _validity_rejected(name: str) -> CandidateRecord:
    return CandidateRecord(
        candidate_hash=name,
        document_ref="ref",
        lineage_parent=None,
        mutation_record=MutationRecord(proposer="p"),
        reflector_input_refs=(),
        valid=False,
        violations=("bad header",),
    )


def test_single_best_is_highest_resolve_within_c_sel() -> None:
    records = [_record("A", 0.6, 1.0), _record("B", 0.7, 5.0), _record("C", 0.8, 100.0)]
    result = select_final(records, c_sel=10.0)
    assert result.single_best is not None
    assert result.single_best.candidate_hash == "B"  # C's 0.8 is over c_sel


def test_single_best_ignores_shaped_minibatch_scores() -> None:
    """A huge minibatch (shaped) score never wins selection — only gate resolve does."""
    records = [
        _record("A", 0.6, 1.0, minibatch={"soft": 0.99}),
        _record("B", 0.7, 5.0),
    ]
    assert select_final(records, c_sel=10.0).single_best.candidate_hash == "B"


def test_single_best_is_none_when_nothing_within_c_sel() -> None:
    records = [_record("A", 0.9, 100.0), _record("B", 0.8, 50.0)]
    assert select_final(records, c_sel=1.0).single_best is None


def test_pareto_frontier_drops_dominated_candidates() -> None:
    records = [
        _record("A", 0.6, 1.0),
        _record("B", 0.7, 5.0),
        _record("C", 0.8, 100.0),
        _record("D", 0.5, 1.0),  # dominated by A (>= resolve, <= cost, strictly better)
    ]
    result = select_final(records, c_sel=200.0)
    hashes = [r.candidate_hash for r in result.pareto]
    assert hashes == ["C", "B", "A"]  # sorted desc resolve; D dominated out


def test_validity_rejected_candidates_are_not_eligible() -> None:
    records = [_record("A", 0.6, 1.0), _validity_rejected("R")]
    result = select_final(records, c_sel=10.0)
    assert result.single_best.candidate_hash == "A"
    assert all(r.candidate_hash != "R" for r in result.pareto)


def test_negative_c_sel_raises() -> None:
    with pytest.raises(ValueError, match="c_sel must be >= 0"):
        select_final([], c_sel=-1.0)


# The selection closure reads GateDecision fields only — pinned structurally.
_PKG_PREFIX = "pydocs_eval."
_PKG_ROOT = Path(pydocs_eval.__file__).parent
_FORBIDDEN = {
    "pydocs_eval.trajectory.shaped_score",
    "pydocs_eval.trajectory.metrics",
    "pydocs_eval.trajectory.consumers",
    "pydocs_eval.trajectory.feedback",
    "pydocs_eval.trajectory.attribution",
}


def _imports_in(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return {n for n in names if n.startswith(_PKG_PREFIX)}


def _reachable(root: Path) -> set[str]:
    seen: set[str] = set()
    frontier = _imports_in(root)
    while frontier:
        module = frontier.pop()
        if module in seen:
            continue
        seen.add(module)
        rel = module.removeprefix(_PKG_PREFIX).replace(".", "/")
        for candidate in (_PKG_ROOT / f"{rel}.py", _PKG_ROOT / rel / "__init__.py"):
            if candidate.exists():
                frontier |= _imports_in(candidate) - seen
    return seen


def test_selection_closure_excludes_shaped_scoring() -> None:
    assert _reachable(Path(selection_mod.__file__)).isdisjoint(_FORBIDDEN)
