"""Feedback-implicated component selector — the flagged targeting seam (ADR 0019 §2).

GEPA's default targeting is ``module_selector="round_robin"`` (one component per
iteration, fully attributable in the ledger). This module is the OPT-IN
alternative: a ``ReflectionComponentSelector`` (verified config-not-code seam,
``proposer/reflective_mutation/base.py``) that reads only the per-component
subsample scores and the Phase 2 feedback facts already in the reflective
dataset, and returns the single section the feedback most implicates.

It consumes NOTHING the reflector does not already see (R6): the tool names it
counts are lifted from the candidate's own ``TOOL: <name>`` component keys, and
the mentions are counted in the bounded Phase 2 feedback strings carried on the
trajectories. Ties break by canonical section order (the candidate's key order),
so the pick is deterministic; an empty implication falls back to the first
mutable section so the loop never stalls.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_eval.optimize.gepa_harness.reflective import InstanceTrajectory

__all__ = ["SELECTOR_NAME", "FeedbackImplicatedSelector"]

SELECTOR_NAME = "feedback_implicated"
_TOOL_PREFIX = "TOOL: "


@dataclass(frozen=True, slots=True)
class FeedbackImplicatedSelector:
    """Pick the one candidate section the feedback facts most implicate.

    Matches GEPA's ``ReflectionComponentSelector`` call shape
    ``(state, trajectories, subsample_scores, candidate_idx, candidate) ->
    list[str]``; ``state`` / ``subsample_scores`` / ``candidate_idx`` are accepted
    for Protocol parity but the pick is driven by the feedback strings on the
    trajectories, so the choice is auditable straight from the reflective dataset.
    """

    name: str = SELECTOR_NAME

    def __call__(
        self,
        state: object,
        trajectories: Sequence[InstanceTrajectory],
        subsample_scores: Sequence[float],
        candidate_idx: int,
        candidate: dict[str, str],
    ) -> list[str]:
        """Return a one-element list naming the implicated section (never empty)."""
        _ = (state, subsample_scores, candidate_idx)  # Protocol parity, unused.
        tool_sections = [key for key in candidate if key.startswith(_TOOL_PREFIX)]
        feedback = " ".join(_feedback_text(t) for t in trajectories)
        implicated = _most_mentioned(tool_sections, feedback)
        return [implicated or _fallback_section(candidate)]


def _feedback_text(traj: object) -> str:
    """The trajectory's feedback string (empty for a shape without one)."""
    return getattr(traj, "feedback", "") or ""


def _most_mentioned(tool_sections: Sequence[str], feedback: str) -> str | None:
    """The ``TOOL:`` section whose tool name appears most in ``feedback``; ties → order."""
    best_section: str | None = None
    best_count = 0
    for section in tool_sections:
        count = feedback.count(section.removeprefix(_TOOL_PREFIX))
        if count > best_count:  # strict > keeps the FIRST (canonical-order) winner
            best_section, best_count = section, count
    return best_section if best_count > 0 else None


def _fallback_section(candidate: dict[str, str]) -> str:
    """First mutable section when nothing is implicated — the loop must not stall.

    Raises:
        ValueError: if the candidate has no sections at all — there is nothing to
            mutate, which is a construction bug upstream of the selector.
    """
    for key in candidate:
        return key
    raise ValueError("candidate has no sections to select; expected >=1 component key")
