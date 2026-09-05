"""Minibatch-margin filter — the gate-cadence screen, structurally NEVER acceptance
(ADR 0018 §Gate cadence, option ii).

A candidate reaches the expensive val gate only if its cheap dev-loop minibatch
shaped score beats the current-best by the pre-registered margin ``m_mb``. This is
the ONE place the shaped score is consulted for flow control, and it is
structurally quarantined from acceptance: the filter's only outputs are
:attr:`FilterDecision.PROCEED` and :attr:`FilterDecision.SKIP` — there is no
ACCEPT — and its output feeds gating *cadence*, never
``gepa_harness.acceptance.decide_acceptance``. ADR 0018's invariant: "minibatches
filter; ONLY the val gate accepts." The asymmetry is proven two ways in the
tests: the enum has exactly two members (no accept outcome to return), and the
module's import closure never reaches the acceptance module (no code path from
the filter can call the acceptance rule).
"""

from __future__ import annotations

import enum

__all__ = ["FilterDecision", "MinibatchMarginUnsetError", "minibatch_filter"]


class MinibatchMarginUnsetError(Exception):
    """The minibatch margin ``m_mb`` is unfilled — the filter refuses to run.

    ``m_mb`` is a ``[TO BE MEASURED]`` Phase 3 noise-probe slot (ADR 0018
    pre-registration). Running the filter with an unset margin would silently
    default a bound the campaign never registered, so the filter raises instead.
    """

    def __init__(self) -> None:
        super().__init__(
            "minibatch-margin filter cannot run: m_mb is unfilled ([TO BE MEASURED]); "
            "the Phase 3 noise probe fills the m_mb slot before any campaign launch (ADR 0018)"
        )


class FilterDecision(enum.Enum):
    """The filter's ONLY two outcomes — PROCEED to the val gate, or SKIP.

    There is deliberately no ACCEPT member: the minibatch filter governs gating
    cadence, and acceptance is a distinct authority
    (``gepa_harness.acceptance.decide_acceptance`` over ground-truth gate inputs).
    The two-member enum is the type-level half of the filter-never-accepts proof.
    """

    PROCEED = "proceed"
    SKIP = "skip"


def minibatch_filter(
    candidate_score: float, best_score: float, m_mb: float | None
) -> FilterDecision:
    """Return PROCEED iff the candidate beats the current-best minibatch score by ``m_mb``.

    ``candidate_score`` / ``best_score`` are dev-loop SHAPED minibatch scores (R2:
    legal in the dev loop, forbidden in acceptance). ``m_mb`` is the pre-registered
    filter margin; when it is ``None`` the Phase 3 noise probe has not filled the
    slot, so the filter REFUSES rather than default a bound.

    Raises:
        MinibatchMarginUnsetError: if ``m_mb`` is ``None`` — an unfilled slot.

    Example:
        >>> minibatch_filter(0.72, 0.70, 0.01)
        <FilterDecision.PROCEED: 'proceed'>
        >>> minibatch_filter(0.70, 0.70, 0.01)
        <FilterDecision.SKIP: 'skip'>
    """
    if m_mb is None:
        raise MinibatchMarginUnsetError()
    beats_margin = candidate_score - best_score >= m_mb
    return FilterDecision.PROCEED if beats_margin else FilterDecision.SKIP
