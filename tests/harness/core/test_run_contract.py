"""harness/core/run_contract — the port every harness implements.

Spec: docs/superpowers/specs/2026-07-27-harness-run-contract-design.md §2.
Core-only: the contract is stdlib + PydocsMCPError, so nothing here needs
an optional extra.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.core.run_contract import (
    REQUIRED_SAMPLE_KEYS,
    HarnessRunner,
    ToolCallObservation,
    ToolCallRecord,
    Trajectory,
    TurnBudgetExceededError,
    UndeliverableGuidanceError,
    missing_sample_keys,
)

_SERVER = ToolCallRecord("search_codebase", "d" * 64, ToolCallObservation.SERVER)
_CLIENT = ToolCallRecord("reinspect_images", "e" * 64, ToolCallObservation.CLIENT)


def _trajectory() -> Trajectory:
    return Trajectory(
        trajectory_id="t1",
        trace_dir=Path("/tmp/traces/t1"),
        answer="the answer",
        tool_calls=(_SERVER, _CLIENT),
        turns=2,
        cost_usd=0.0,
        wall_seconds=1.5,
    )


def test_required_sample_keys_are_the_ratified_tuple() -> None:
    assert REQUIRED_SAMPLE_KEYS == ("record_id", "task_name", "rendered_prompt", "gold")


def test_missing_sample_keys_names_exactly_the_absent_ones() -> None:
    assert missing_sample_keys({"record_id": "r", "task_name": "t"}) == (
        "rendered_prompt",
        "gold",
    )
    assert missing_sample_keys(dict.fromkeys(REQUIRED_SAMPLE_KEYS, "x")) == ()


def test_server_tool_calls_filters_to_the_authoritative_slice() -> None:
    assert _trajectory().server_tool_calls() == (_SERVER,)


def test_observation_values_are_plain_english_strings() -> None:
    assert ToolCallObservation.SERVER == "server"
    assert ToolCallObservation.CLIENT == "client"


def test_undeliverable_guidance_error_names_sections_and_deliverable_set() -> None:
    with pytest.raises(UndeliverableGuidanceError) as excinfo:
        raise UndeliverableGuidanceError(
            sections=("SKILL",), deliverable=("BACKBONE", "SYSTEM_PROMPT")
        )
    message = str(excinfo.value)
    assert "SKILL" in message and "BACKBONE" in message
    assert isinstance(excinfo.value, PydocsMCPError)
    assert isinstance(excinfo.value, ValueError)


def test_turn_budget_error_carries_the_limit() -> None:
    with pytest.raises(TurnBudgetExceededError) as excinfo:
        raise TurnBudgetExceededError(turn_limit=8)
    assert excinfo.value.turn_limit == 8
    assert "8" in str(excinfo.value)
    assert isinstance(excinfo.value, RuntimeError)


def test_a_turn_budget_error_raised_with_the_limit_alone_is_unchanged() -> None:
    """The external harness raises it this way: no trace, no spend, the cap as its turns."""
    error = TurnBudgetExceededError(turn_limit=8)

    assert str(error) == "no final answer within the turn budget of 8 turns"
    assert (error.cost_usd, error.trajectory_id, error.trace_dir) == (0.0, "", Path())
    assert error.turns == 8


def test_a_turn_budget_error_can_carry_the_trace_it_left() -> None:
    error = TurnBudgetExceededError(
        turn_limit=12, trajectory_id="t9", trace_dir=Path("/tmp/traces/t9"), turns=11
    )

    assert (error.trajectory_id, error.trace_dir, error.turns) == (
        "t9",
        Path("/tmp/traces/t9"),
        11,
    )
    assert error.turn_limit == 12


def test_a_trajectory_ends_answered_unless_a_flag_says_otherwise() -> None:
    """Null defaults: every existing constructor keeps building an ordinary run."""
    assert (_trajectory().budget_exhausted, _trajectory().timed_out) == (False, False)


def test_the_not_confirmed_label_is_the_contract_spelling() -> None:
    from pydocs_mcp.harness.core.run_contract import NOT_CONFIRMED_LABEL

    assert NOT_CONFIRMED_LABEL == "Not confirmed:"


def test_harness_runner_is_structurally_checkable() -> None:
    # A conforming harness never imports the Protocol — structural typing is
    # what keeps toolkit packages decoupled from the product (constraint C3).
    class _StructuralRunner:
        async def run(self, sample, guidance_sections):
            return _trajectory()

    assert isinstance(_StructuralRunner(), HarnessRunner)
    assert not isinstance(object(), HarnessRunner)
