"""How one ask run ended: the outcome taxonomy, its one truth table, and the mirrors.

The outcome is what turns a bare turn count into Turns-to-answer: a question
that ran out of budget, timed out, or came back empty never answered, so it is
never averaged in as if it had. These pin the truth table row by row, the eval
mirrors of the product's two literals, and the back-fill that reads an
``arm.json`` written before outcomes were recorded.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.trajectory.ask_outcome import (
    ASK_BUDGET_EXHAUSTED_REPLY,
    ASK_NOT_CONFIRMED_LABEL,
    RunEvidence,
    TaskOutcome,
    is_near_cap,
    legacy_outcome_of,
    outcome_of,
    penalised_turns,
    run_evidence,
)


def _evidence(**facts: bool) -> RunEvidence:
    """An answered run, unless a fact says otherwise."""
    defaults = {
        "timed_out": False,
        "budget_exhausted": False,
        "answer_empty": False,
        "reply_starved": False,
    }
    return RunEvidence(**{**defaults, **facts})


# --- the truth table ------------------------------------------------------


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({}, TaskOutcome.ANSWERED),
        ({"answer_empty": True}, TaskOutcome.UNANSWERED_EMPTY),
        ({"answer_empty": True, "reply_starved": True}, TaskOutcome.STARVED_REPLY),
        # A starved finish on a reply that DID answer is not starvation.
        ({"reply_starved": True}, TaskOutcome.ANSWERED),
        ({"budget_exhausted": True, "answer_empty": True}, TaskOutcome.BUDGET_EXHAUSTED),
        ({"budget_exhausted": True}, TaskOutcome.EXHAUSTED_FINALIZED),
        (
            {"budget_exhausted": True, "answer_empty": True, "reply_starved": True},
            TaskOutcome.BUDGET_EXHAUSTED,
        ),
        ({"timed_out": True, "answer_empty": True}, TaskOutcome.TIMEOUT),
        # A killed run is a timeout whatever else it carried — never an empty answer.
        ({"timed_out": True, "budget_exhausted": True, "answer_empty": True}, TaskOutcome.TIMEOUT),
        ({"timed_out": True, "answer_empty": True, "reply_starved": True}, TaskOutcome.TIMEOUT),
    ],
)
def test_the_truth_table_decides_exactly_one_outcome(
    facts: dict[str, bool], expected: TaskOutcome
) -> None:
    assert outcome_of(_evidence(**facts)) is expected


def test_the_taxonomy_is_the_seven_outcomes_in_decision_order() -> None:
    assert [outcome.value for outcome in TaskOutcome] == [
        "unrecorded",
        "timeout",
        "budget_exhausted",
        "exhausted_finalized",
        "starved_reply",
        "unanswered_empty",
        "answered",
    ]


# --- evidence off a finished run --------------------------------------------


@dataclass(frozen=True, slots=True)
class OldProductTrajectory:
    """What a product built before issue #371 returns: no outcome flags at all."""

    answer: str
    turns: int = 3
    trace_dir: Path = field(default_factory=Path)


@dataclass(frozen=True, slots=True)
class FlaggedTrajectory:
    """What a product with issue #371's fields returns."""

    answer: str
    budget_exhausted: bool = False
    timed_out: bool = False
    turns: int = 3


def test_an_old_products_canned_apology_reads_as_budget_exhausted() -> None:
    """Before 2b the sentinel comes back AS the answer; it is not one."""
    evidence = run_evidence(
        OldProductTrajectory(answer=ASK_BUDGET_EXHAUSTED_REPLY),
        last_finish_reason="",
        thinking_off=False,
    )

    assert outcome_of(evidence) is TaskOutcome.BUDGET_EXHAUSTED


def test_a_flagged_exhausted_run_with_an_empty_answer_is_budget_exhausted() -> None:
    evidence = run_evidence(
        FlaggedTrajectory(answer="", budget_exhausted=True),
        last_finish_reason="",
        thinking_off=False,
    )

    assert outcome_of(evidence) is TaskOutcome.BUDGET_EXHAUSTED


def test_a_flagged_exhausted_run_that_still_answered_is_finalized() -> None:
    evidence = run_evidence(
        FlaggedTrajectory(answer="It lives in a.py.", budget_exhausted=True),
        last_finish_reason="",
        thinking_off=False,
    )

    assert outcome_of(evidence) is TaskOutcome.EXHAUSTED_FINALIZED


def test_a_timed_out_run_is_a_timeout_never_an_empty_answer() -> None:
    evidence = run_evidence(
        FlaggedTrajectory(answer="", timed_out=True), last_finish_reason="", thinking_off=False
    )

    assert outcome_of(evidence) is TaskOutcome.TIMEOUT


def test_an_empty_reply_cut_at_length_is_starved_while_thinking_is_on() -> None:
    evidence = run_evidence(
        OldProductTrajectory(answer="  "), last_finish_reason="length", thinking_off=False
    )

    assert outcome_of(evidence) is TaskOutcome.STARVED_REPLY


def test_an_arm_that_turns_thinking_off_never_books_a_starved_reply() -> None:
    evidence = run_evidence(
        OldProductTrajectory(answer=""), last_finish_reason="length", thinking_off=True
    )

    assert outcome_of(evidence) is TaskOutcome.UNANSWERED_EMPTY


def test_an_empty_reply_that_stopped_normally_is_unanswered() -> None:
    evidence = run_evidence(
        OldProductTrajectory(answer=""), last_finish_reason="stop", thinking_off=False
    )

    assert outcome_of(evidence) is TaskOutcome.UNANSWERED_EMPTY


# --- the legacy back-fill ---------------------------------------------------


def test_the_47_char_12_turn_row_under_a_12_turn_cap_back_fills_as_exhausted() -> None:
    assert len(ASK_BUDGET_EXHAUSTED_REPLY) == 47
    outcome = legacy_outcome_of(answer_chars=47, turns=12, max_agent_turns=12)

    assert outcome is TaskOutcome.BUDGET_EXHAUSTED


@pytest.mark.parametrize(("answer_chars", "turns"), [(47, 11), (48, 12), (2099, 12)])
def test_any_other_legacy_row_back_fills_as_answered(answer_chars: int, turns: int) -> None:
    outcome = legacy_outcome_of(answer_chars=answer_chars, turns=turns, max_agent_turns=12)

    assert outcome is TaskOutcome.ANSWERED


def test_a_legacy_row_with_no_answer_back_fills_as_unanswered() -> None:
    """The row records its answer's length, so an empty one is known to be empty."""
    outcome = legacy_outcome_of(answer_chars=0, turns=4, max_agent_turns=12)

    assert outcome is TaskOutcome.UNANSWERED_EMPTY


def test_a_legacy_row_without_a_known_cap_stays_unrecorded() -> None:
    """Exhaustion is ``turns == cap``; with no cap the honest answer is "unknown"."""
    outcome = legacy_outcome_of(answer_chars=47, turns=12, max_agent_turns=0)

    assert outcome is TaskOutcome.UNRECORDED


# --- near the cap, and the penalty --------------------------------------------


@pytest.mark.parametrize(("turns", "near"), [(10, False), (11, True), (12, True)])
def test_near_cap_is_within_one_turn_of_the_budget(turns: int, near: bool) -> None:
    assert is_near_cap(turns, max_agent_turns=12) is near


def test_near_cap_is_false_when_the_cap_is_unknown() -> None:
    assert is_near_cap(12, max_agent_turns=0) is False


def test_an_answered_task_is_charged_its_own_turns() -> None:
    assert penalised_turns(TaskOutcome.ANSWERED, turns=12, max_agent_turns=12) == 12


@pytest.mark.parametrize(
    "outcome",
    [
        TaskOutcome.TIMEOUT,
        TaskOutcome.BUDGET_EXHAUSTED,
        TaskOutcome.EXHAUSTED_FINALIZED,
        TaskOutcome.STARVED_REPLY,
        TaskOutcome.UNANSWERED_EMPTY,
    ],
)
def test_every_unanswered_outcome_is_charged_the_budget_plus_one(outcome: TaskOutcome) -> None:
    assert penalised_turns(outcome, turns=3, max_agent_turns=12) == 13


def test_an_unrecorded_task_has_no_penalised_turns() -> None:
    assert penalised_turns(TaskOutcome.UNRECORDED, turns=3, max_agent_turns=12) is None


def test_an_unanswered_task_under_an_unknown_cap_has_no_penalised_turns() -> None:
    """``0 + 1`` would be a fabricated budget, not a measured one."""
    assert penalised_turns(TaskOutcome.BUDGET_EXHAUSTED, turns=3, max_agent_turns=0) is None


# --- the mirrors: equal to the product's literals once issue #371 lands -------


def _product_attribute(module_name: str, attribute: str) -> str:
    """The product's literal, or a skip while the product predates it.

    WHY ``getattr`` and not ``importorskip``: the MODULE exists today; it is the
    ATTRIBUTE that issue #371 adds, so the module-level skip would never fire.
    """
    module = importlib.import_module(module_name)
    literal = getattr(module, attribute, None)
    if literal is None:
        pytest.skip(f"{module_name}.{attribute} lands with issue #371; checked then")
    return str(literal)


def test_the_budget_exhausted_mirror_equals_the_product_constant() -> None:
    pytest.importorskip("pydocs_mcp")
    literal = _product_attribute(
        "pydocs_mcp.harness.ask_your_docs.turn_budget", "BUDGET_EXHAUSTED_REPLY"
    )

    assert literal == ASK_BUDGET_EXHAUSTED_REPLY


def test_the_not_confirmed_mirror_equals_the_product_constant() -> None:
    pytest.importorskip("pydocs_mcp")
    literal = _product_attribute("pydocs_mcp.harness.core.run_contract", "NOT_CONFIRMED_LABEL")

    assert literal == ASK_NOT_CONFIRMED_LABEL
