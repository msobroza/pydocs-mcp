"""How one ask-your-docs run ended: the outcome taxonomy, decided by one truth table.

A turn count alone cannot say whether a question was answered. LangGraph's
prebuilt agent swaps the reply it would have made at the turn cap for a canned
apology, so a run that exhausted its budget used to come back looking like a
12-turn answer; a killed run and a reply the model starved while thinking come
back as an empty answer. Turns-to-answer is only meaningful once each task says
which of these it was, so every run gets exactly ONE :class:`TaskOutcome`:

========================  ====================================================
``UNRECORDED``            an ``arm.json`` row written before outcomes existed,
                          whose outcome could not be back-filled
``TIMEOUT``               the eval's per-task timeout killed the run
``BUDGET_EXHAUSTED``      the budget ran out and no answer came back
``EXHAUSTED_FINALIZED``   the budget ran out, and a final reply still answered
``STARVED_REPLY``         an empty reply the endpoint cut at ``length`` while
                          the arm let the model think
``UNANSWERED_EMPTY``      an empty answer, for no reason above
``ANSWERED``              anything else
========================  ====================================================

:func:`outcome_of` is the one truth table; the two evidence builders only say
what a finished run (:func:`run_evidence`) or a legacy row
(:func:`legacy_outcome_of`) knows.

**Old products still measure.** The product's ``Trajectory`` gains
``budget_exhausted`` / ``timed_out`` with issue #371; until then every flag is read
with a ``getattr`` default, and an old product's exhausted run is recognised by
its answer — the canned apology itself, mirrored here as
:data:`ASK_BUDGET_EXHAUSTED_REPLY`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# Mirrors ``pydocs_mcp.harness.ask_your_docs.turn_budget.BUDGET_EXHAUSTED_REPLY``
# (issue #371), itself LangGraph's literal (``langgraph.prebuilt``
# ``chat_agent_executor``) for the reply that replaces the last tool-calling one
# at the step limit. Mirrored and not imported, like ``ASK_MODEL_USAGE_FILENAME``:
# the trajectory package keeps a zero-``pydocs_mcp`` floor, and an eval running
# over a product that predates the constant must still recognise the reply —
# which is why this mirror lands first and its parity test skips until then.
ASK_BUDGET_EXHAUSTED_REPLY = "Sorry, need more steps to process this request."

# Mirrors ``pydocs_mcp.harness.core.run_contract.NOT_CONFIRMED_LABEL`` (issue #371):
# the label a finalized answer puts before what it could not confirm. Landed here
# with its sibling so the answer scorer and the completeness rows read one
# spelling; nothing in this module reads it yet.
ASK_NOT_CONFIRMED_LABEL = "Not confirmed:"

# The ``finish_reason`` an OpenAI-format endpoint reports for a reply cut at
# ``max_tokens`` — the product's starved-reply rule (``chat_wire.reply_starved``).
STARVED_FINISH_REASON = "length"


class TaskOutcome(StrEnum):
    """How one task's run ended — mutually exclusive, in decision order."""

    UNRECORDED = "unrecorded"
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    EXHAUSTED_FINALIZED = "exhausted_finalized"
    STARVED_REPLY = "starved_reply"
    UNANSWERED_EMPTY = "unanswered_empty"
    ANSWERED = "answered"

    @property
    def is_recorded(self) -> bool:
        """False only for a legacy row whose outcome nobody could establish."""
        return self is not TaskOutcome.UNRECORDED

    @property
    def is_budget_exhausted(self) -> bool:
        """True when the run hit its turn budget — finalized afterwards or not."""
        return self in (TaskOutcome.BUDGET_EXHAUSTED, TaskOutcome.EXHAUSTED_FINALIZED)


@dataclass(frozen=True, slots=True)
class RunEvidence:
    """The four facts the truth table reads, each a plain observation of one run.

    ``reply_starved`` is the product's rule already applied: the last metered
    reply ended on ``length`` while the arm did not turn thinking off.
    """

    timed_out: bool
    budget_exhausted: bool
    answer_empty: bool
    reply_starved: bool


def outcome_of(evidence: RunEvidence) -> TaskOutcome:
    """THE truth table: one run's evidence in, exactly one outcome out.

    Order is the contract: a killed run is a timeout whatever else it carried,
    an exhausted run is exhausted whether or not a final reply answered, and a
    starved reply only matters when the answer came back empty.

    Example:
        >>> outcome_of(RunEvidence(False, False, answer_empty=True, reply_starved=False))
        <TaskOutcome.UNANSWERED_EMPTY: 'unanswered_empty'>
    """
    if evidence.timed_out:
        return TaskOutcome.TIMEOUT
    if evidence.budget_exhausted:
        if evidence.answer_empty:
            return TaskOutcome.BUDGET_EXHAUSTED
        return TaskOutcome.EXHAUSTED_FINALIZED
    if not evidence.answer_empty:
        return TaskOutcome.ANSWERED
    if evidence.reply_starved:
        return TaskOutcome.STARVED_REPLY
    return TaskOutcome.UNANSWERED_EMPTY


def is_budget_exhausted_answer(answer: str) -> bool:
    """True when ``answer`` IS LangGraph's canned apology — exact bytes, nothing else."""
    return answer == ASK_BUDGET_EXHAUSTED_REPLY


def run_evidence(trajectory: Any, *, last_finish_reason: str, thinking_off: bool) -> RunEvidence:
    """What one finished run says about how it ended.

    Duck-typed on the product ``Trajectory``, with ``getattr`` defaults for the
    two flags issue #371 adds. ``last_finish_reason`` is the last metered reply's
    (``token_accounting.last_finish_reason``); ``""`` when none was recorded.
    """
    answer = str(getattr(trajectory, "answer", ""))
    apology = is_budget_exhausted_answer(answer)
    return RunEvidence(
        timed_out=bool(getattr(trajectory, "timed_out", False)),
        budget_exhausted=bool(getattr(trajectory, "budget_exhausted", False)) or apology,
        answer_empty=apology or not answer.strip(),
        reply_starved=last_finish_reason == STARVED_FINISH_REASON and not thinking_off,
    )


def legacy_outcome_of(*, answer_chars: int, turns: int, max_agent_turns: int) -> TaskOutcome:
    """The outcome of an ``arm.json`` row written before outcomes were recorded.

    Such a row keeps only its answer's LENGTH, so the canned apology is known by
    its 47 characters AND the full budget spent — a real 47-character answer
    is possible, a real one at exactly the cap far less so. An empty answer is
    known to be empty. With no cap to compare against nothing can be told
    apart, and the row stays ``UNRECORDED``.

    Example:
        >>> legacy_outcome_of(answer_chars=47, turns=12, max_agent_turns=12)
        <TaskOutcome.BUDGET_EXHAUSTED: 'budget_exhausted'>
    """
    if max_agent_turns <= 0:
        return TaskOutcome.UNRECORDED
    apology = answer_chars == len(ASK_BUDGET_EXHAUSTED_REPLY) and turns == max_agent_turns
    return outcome_of(
        RunEvidence(
            timed_out=False,
            budget_exhausted=apology,
            answer_empty=apology or answer_chars == 0,
            reply_starved=False,
        )
    )


def is_near_cap(turns: int, *, max_agent_turns: int) -> bool:
    """True when a run spent its whole budget or all but one turn of it."""
    return max_agent_turns > 0 and turns >= max_agent_turns - 1


def penalised_turns(outcome: TaskOutcome, *, turns: int, max_agent_turns: int) -> int | None:
    """Turns-to-answer as the headline counts it: unanswered costs the budget + 1.

    An unanswered question did not take fewer turns than the budget — it never
    finished — so it is charged one turn more than any answer could take. The
    penalty is derived here, at measurement, and never stored in ``turns``.
    ``None`` (dropped from the mean) for an unrecorded outcome, and for an
    unanswered one whose budget is unknown.
    """
    if outcome is TaskOutcome.ANSWERED:
        return turns
    if not outcome.is_recorded or max_agent_turns <= 0:
        return None
    return max_agent_turns + 1


@dataclass(frozen=True, slots=True)
class TaskEnding:
    """How one task ended, and every figure that follows from it.

    ``turns`` is the graph's own count — the answering reply included, a
    finalize reply excluded — so a finalized task still reads the budget here;
    ``None`` when nothing was measured. ``max_agent_turns`` is the budget the
    task ran under, ``0`` when unknown. Everything else is DERIVED, so no two
    figures on one task can disagree about how it ended.
    """

    outcome: TaskOutcome
    turns: int | None
    max_agent_turns: int

    @property
    def near_cap(self) -> bool:
        return self.turns is not None and is_near_cap(
            self.turns, max_agent_turns=self.max_agent_turns
        )

    @property
    def turns_to_answer_penalised(self) -> int | None:
        """Turns-to-answer with every unanswered outcome charged the budget + 1."""
        if self.turns is None:
            return None
        return penalised_turns(self.outcome, turns=self.turns, max_agent_turns=self.max_agent_turns)

    @property
    def turns_to_answer_answered_only(self) -> int | None:
        """The task's turns when it answered; ``None`` for every other outcome."""
        return self.turns if self.outcome is TaskOutcome.ANSWERED else None

    @property
    def answered_within_budget(self) -> int | None:
        """1 when the task answered, 0 when it did not; ``None`` when unrecorded."""
        if not self.outcome.is_recorded:
            return None
        return int(self.outcome is TaskOutcome.ANSWERED)

    @property
    def budget_exhausted(self) -> int | None:
        """1 when the task hit its budget, finalized or not; ``None`` when unrecorded."""
        if not self.outcome.is_recorded:
            return None
        return int(self.outcome.is_budget_exhausted)


#: The ending of a task nothing measured — every figure it derives is undefined.
UNMEASURED_ENDING = TaskEnding(outcome=TaskOutcome.UNRECORDED, turns=None, max_agent_turns=0)


__all__ = (
    "ASK_BUDGET_EXHAUSTED_REPLY",
    "ASK_NOT_CONFIRMED_LABEL",
    "STARVED_FINISH_REASON",
    "UNMEASURED_ENDING",
    "RunEvidence",
    "TaskEnding",
    "TaskOutcome",
    "is_budget_exhausted_answer",
    "is_near_cap",
    "legacy_outcome_of",
    "outcome_of",
    "penalised_turns",
    "run_evidence",
)
