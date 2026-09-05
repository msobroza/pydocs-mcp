"""Final selection over gate-accepted candidates (ADR 0020 §Selection rule).

Consumes ONLY ``GateDecision`` fields recorded on each candidate's ledger entry —
``resolve_rate`` and ``cost_usd`` — never a shaped minibatch score and never
GEPA's internal Pareto frontier (which is over shaped scores, R2). Two outputs the
owner sees at the freeze checkpoint:

- the **computed single-best**: the candidate with the highest val
  ``GateDecision.resolve_rate`` whose ``cost_usd`` is within the pre-registered
  ``c_sel`` (ADR 0018 slot);
- the **small Pareto set** over ``(val resolve_rate, cost_usd)`` — the trade-off
  frontier the owner may freeze an alternate from (e.g. a point of resolve for
  materially lower cost).

Selection is mechanical and R2-clean; the freeze itself is the owner's, made with
the frontier visible (ADR 0020 §Options a). Candidates without a recorded gate
decision (validity-rejected — zero rollouts) are not selection-eligible and are
skipped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_eval.optimize.candidates.ledger import CandidateRecord

__all__ = ["SelectionResult", "select_final"]


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """The owner-facing selection view (ADR 0020 §Selection rule).

    ``single_best`` is the computed recommendation (``None`` when no gated
    candidate is within ``c_sel``); ``pareto`` is the small non-dominated frontier
    over ``(resolve_rate, cost_usd)``, sorted by descending resolve then ascending
    cost for a stable presentation.
    """

    single_best: CandidateRecord | None
    pareto: tuple[CandidateRecord, ...]


def select_final(candidates: Sequence[CandidateRecord], c_sel: float) -> SelectionResult:
    """Compute the single-best (within ``c_sel``) + the Pareto frontier from the ledger.

    Operates on gate-accepted candidates: each must carry a ``GateOutcome`` (its
    ``resolve_rate`` + ``cost_usd`` are the only fields read). ``c_sel`` bounds the
    single-best's cost; the Pareto set is over every gated candidate so the owner
    can weigh a cheaper alternate.

    Raises:
        ValueError: if ``c_sel`` is negative — a dollar threshold, not a delta.
    """
    if c_sel < 0:
        raise ValueError(f"c_sel must be >= 0, got {c_sel!r}; it is a cost threshold")
    gated = [record for record in candidates if record.gate is not None]
    return SelectionResult(single_best=_single_best(gated, c_sel), pareto=_pareto(gated))


def _sort_key(record: CandidateRecord) -> tuple[float, float, str]:
    """Descending resolve, then ascending cost, then hash — a total, stable order."""
    gate = record.gate
    assert gate is not None  # callers pass only gated records
    return (-gate.resolve_rate, gate.cost_usd, record.candidate_hash)


def _single_best(gated: list[CandidateRecord], c_sel: float) -> CandidateRecord | None:
    """Highest val ``resolve_rate`` whose ``cost_usd`` is within ``c_sel``."""
    eligible = [record for record in gated if record.gate.cost_usd <= c_sel]  # type: ignore[union-attr]
    return min(eligible, key=_sort_key) if eligible else None


def _pareto(gated: list[CandidateRecord]) -> tuple[CandidateRecord, ...]:
    """The non-dominated frontier over ``(maximize resolve_rate, minimize cost)``."""
    frontier = [r for r in gated if not any(_dominates(o, r) for o in gated if o is not r)]
    return tuple(sorted(frontier, key=_sort_key))


def _dominates(a: CandidateRecord, b: CandidateRecord) -> bool:
    """``a`` dominates ``b``: no worse on both axes and strictly better on one."""
    ga, gb = a.gate, b.gate
    assert ga is not None  # callers pass only gated records
    assert gb is not None
    no_worse = ga.resolve_rate >= gb.resolve_rate and ga.cost_usd <= gb.cost_usd
    strictly_better = ga.resolve_rate > gb.resolve_rate or ga.cost_usd < gb.cost_usd
    return no_worse and strictly_better
