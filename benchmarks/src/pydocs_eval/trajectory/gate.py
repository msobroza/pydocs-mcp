"""The ground-truth validation gate — isolated by construction (ADR 0012, R4).

This module is the ONLY place the acceptance gate is computed, and it consumes
**ground-truth resolve + cost and structurally nothing else**. The isolation is
enforced three ways (ADR 0012 gate-isolation locks):

1. :class:`GroundTruthOutcome` is constructible only from the eval-report parser
   (its factories live in ``eval_report``); no constructor path accepts trace
   metrics or shaped scores.
2. :func:`run_gate`'s signature accepts only ``GroundTruthOutcome`` values and a
   ``cost_usd`` float — no float-bearing metric container type appears in it, so
   passing a shaped score is a *type error*, not a code-review catch.
3. This module does NOT import the shaped-score / metric modules — pinned by an
   import-graph test that fails the suite if the edge ever appears.

``infra_error`` outcomes are excluded from the graded denominator (ADR 0012): an
eval-harness failure is not the model's failure. The gate produces the holdout
ground-truth fitness (resolve rate) that plugs into the orchestrator's existing
final-rung fitness seam.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_eval.trajectory.eval_report import GroundTruthOutcome


@dataclass(frozen=True, slots=True)
class GateDecision:
    """The gate's verdict for one run — ground-truth resolve rate + cost only.

    ``resolve_rate`` is the fraction of GRADED (non-infra) outcomes that resolved;
    ``n_graded`` / ``n_infra_excluded`` make the denominator auditable.
    ``within_budget`` is ``True`` when no budget was supplied or cost is under it.
    ``passed`` requires both a full resolve rate is NOT assumed — the rate itself
    is the fitness the orchestrator compares; ``passed`` only gates on budget.
    """

    resolve_rate: float
    n_graded: int
    n_infra_excluded: int
    cost_usd: float
    within_budget: bool
    passed: bool


def run_gate(
    outcomes: Sequence[GroundTruthOutcome],
    cost_usd: float,
    *,
    max_usd: float | None = None,
) -> GateDecision:
    """Compute the ground-truth gate verdict from resolve outcomes + cost ONLY.

    ``infra_error`` outcomes are excluded from the graded denominator. An empty
    graded set yields a ``0.0`` resolve rate (nothing resolved). ``passed`` is the
    within-budget flag — the resolve rate is the fitness scalar the orchestrator
    compares between seed and candidate, not a hardcoded threshold here.

    Example:
        >>> from pydocs_eval.trajectory.eval_report import infra_outcome, no_report_outcome
        >>> run_gate([no_report_outcome("i")], 1.0, max_usd=5.0).resolve_rate
        0.0
    """
    graded = [o for o in outcomes if not o.infra_error]
    n_infra = len(outcomes) - len(graded)
    resolve_rate = _resolve_rate(graded)
    within_budget = max_usd is None or cost_usd <= max_usd
    return GateDecision(
        resolve_rate=resolve_rate,
        n_graded=len(graded),
        n_infra_excluded=n_infra,
        cost_usd=cost_usd,
        within_budget=within_budget,
        passed=within_budget,
    )


def _resolve_rate(graded: list[GroundTruthOutcome]) -> float:
    """Fraction of graded outcomes that resolved; ``0.0`` on an empty graded set."""
    if not graded:
        return 0.0
    return sum(1 for o in graded if o.resolved) / len(graded)
