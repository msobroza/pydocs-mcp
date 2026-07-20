"""The minibatch-margin filter (ADR 0018 §Gate cadence): PROCEED/SKIP only, and a
structural proof it can never reach acceptance (the filter-never-accepts asymmetry).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pydocs_eval
import pytest

from pydocs_eval.optimize import minibatch_filter as filter_mod
from pydocs_eval.optimize.minibatch_filter import (
    FilterDecision,
    MinibatchMarginUnsetError,
    minibatch_filter,
)


def test_proceeds_when_margin_is_beaten() -> None:
    assert minibatch_filter(0.72, 0.70, 0.01) is FilterDecision.PROCEED


def test_proceeds_at_exactly_the_margin() -> None:
    """The ``>=`` boundary: beating the margin exactly still proceeds."""
    assert minibatch_filter(0.71, 0.70, 0.01) is FilterDecision.PROCEED


def test_skips_when_margin_is_not_beaten() -> None:
    assert minibatch_filter(0.705, 0.70, 0.01) is FilterDecision.SKIP


def test_skips_a_regression() -> None:
    assert minibatch_filter(0.60, 0.70, 0.01) is FilterDecision.SKIP


def test_unset_margin_refuses_to_run_naming_the_slot() -> None:
    with pytest.raises(MinibatchMarginUnsetError, match="m_mb"):
        minibatch_filter(0.9, 0.1, None)


# --------------------------------------------------------------------------
# THE ASYMMETRY: the filter can only PROCEED/SKIP and never reaches acceptance
# --------------------------------------------------------------------------


def test_filter_decision_has_only_proceed_and_skip() -> None:
    """Type-level half of the proof: there is no ACCEPT outcome to return."""
    assert set(FilterDecision) == {FilterDecision.PROCEED, FilterDecision.SKIP}


_PKG_ROOT = Path(pydocs_eval.__file__).parent
_PKG_PREFIX = "pydocs_eval."
_ACCEPTANCE = "pydocs_eval.optimize.gepa_harness.acceptance"
_FORBIDDEN = {
    _ACCEPTANCE,
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


def test_filter_closure_never_reaches_acceptance_or_scoring() -> None:
    """Structural half of the proof: no import path from the filter reaches the
    acceptance rule (or any shaped-score module), so its output cannot flow into
    ``decide_acceptance``'s accept outcome — only into gating cadence."""
    reachable = _reachable(Path(filter_mod.__file__))
    assert reachable.isdisjoint(_FORBIDDEN), reachable & _FORBIDDEN
