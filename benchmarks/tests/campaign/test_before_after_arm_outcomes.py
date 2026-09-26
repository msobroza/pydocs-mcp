"""campaign/before-after — what an arm records about how each task ended.

Budget exhaustion, timeouts, starved replies and empty answers used to be
invisible: an arm booked LangGraph's canned apology as a 12-turn answer. These
pin what an arm writes into ``arm.json``, when a run counts as complete at all,
and how an ``arm.json`` written before outcomes existed is read back.

Every trace here is written by the PRODUCT recorder, so the arm reads the file
shapes a paid run produces; the harness runner itself is a named fake.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after_arm import read_arm_summary
from pydocs_eval.trajectory.ask_outcome import (
    ASK_BUDGET_EXHAUSTED_REPLY,
    UNKNOWN_TURN_BUDGET,
    TaskOutcome,
)

from ._fakes import RecordedTrajectory, ScriptedArmRunner, recorded_trajectory
from ._outcome_fixtures import (
    CAP,
    arm_settings,
    legacy_row,
    run_one_arm,
    stored_arm_json,
    write_legacy_arm,
)

# --- what a new arm writes --------------------------------------------------


def test_a_new_arm_records_the_answer_its_outcome_its_calls_and_near_cap(tmp_path: Path) -> None:
    settings = arm_settings(tmp_path)
    run = replace(
        recorded_trajectory(tmp_path / "traces", answer="It is `a.py`.", turns=11),
        tool_call_count=3,
    )

    run_one_arm(settings, ScriptedArmRunner({"t1": run}), "t1")

    stored = stored_arm_json(settings)
    [row] = stored["tasks"]  # type: ignore[misc]
    assert row["answer"] == "It is `a.py`."
    assert row["answer_chars"] == len("It is `a.py`.")
    assert row["outcome"] == "answered"
    assert row["tool_calls"] == 3
    assert row["near_cap"] is True
    assert stored["max_agent_turns"] == CAP


def test_the_answer_text_round_trips_through_arm_json(tmp_path: Path) -> None:
    settings = arm_settings(tmp_path)
    answer = "Line one.\nNot confirmed: the caller — été → `pkg/b.py`."
    run = recorded_trajectory(tmp_path / "traces", answer=answer, turns=3)

    run_one_arm(settings, ScriptedArmRunner({"t1": run}), "t1")

    [task] = read_arm_summary(Path(settings.out_dir)).tasks
    assert task.answer == answer
    assert task.outcome is TaskOutcome.ANSWERED
    assert task.near_cap is False


def test_an_old_products_exhausted_run_is_booked_once_as_budget_exhausted(
    tmp_path: Path,
) -> None:
    """Before issue #371 the canned apology comes back as the answer at the cap."""
    settings = arm_settings(tmp_path)
    run = recorded_trajectory(tmp_path / "traces", answer=ASK_BUDGET_EXHAUSTED_REPLY, turns=CAP)
    runner = ScriptedArmRunner({"t1": run})

    [task] = run_one_arm(settings, runner, "t1").tasks

    assert runner.seen == ["t1"]
    assert (task.outcome, task.turns) == (TaskOutcome.BUDGET_EXHAUSTED, CAP)
    # The apology is not an answer: the record keeps none, as a newer product's would.
    assert (task.answer, task.answer_chars) == ("", 0)


def test_a_flagged_exhausted_run_with_its_trace_is_booked_as_budget_exhausted(
    tmp_path: Path,
) -> None:
    """Issue #371's shape: the flag set, the apology dropped, the trace kept."""
    settings = arm_settings(tmp_path)
    run = recorded_trajectory(tmp_path / "traces", answer="", turns=CAP, budget_exhausted=True)
    runner = ScriptedArmRunner({"t1": run})

    [task] = run_one_arm(settings, runner, "t1").tasks

    assert runner.seen == ["t1"]
    assert (task.outcome, task.turns) == (TaskOutcome.BUDGET_EXHAUSTED, CAP)


def test_a_timed_out_run_with_its_trace_reads_timeout_not_an_empty_answer(
    tmp_path: Path,
) -> None:
    settings = arm_settings(tmp_path)
    run = recorded_trajectory(tmp_path / "traces", answer="", turns=4, timed_out=True)

    [task] = run_one_arm(settings, ScriptedArmRunner({"t1": run}), "t1").tasks

    assert task.outcome is TaskOutcome.TIMEOUT


def test_an_empty_reply_cut_at_length_is_a_starved_reply_while_thinking_is_on(
    tmp_path: Path,
) -> None:
    settings = arm_settings(tmp_path, llm_block={"params": {"thinking": "auto"}})
    run = recorded_trajectory(tmp_path / "traces", answer="", turns=5, last_finish_reason="length")

    [task] = run_one_arm(settings, ScriptedArmRunner({"t1": run}), "t1").tasks

    assert task.outcome is TaskOutcome.STARVED_REPLY


@pytest.mark.parametrize("thinking", ["off", False])
def test_an_arm_that_turns_thinking_off_books_the_same_reply_as_unanswered(
    tmp_path: Path, thinking: object
) -> None:
    """``False`` is what a YAML 1.1 loader makes of a bare ``thinking: off``."""
    settings = arm_settings(tmp_path, llm_block={"params": {"thinking": thinking}})
    run = recorded_trajectory(tmp_path / "traces", answer="", turns=5, last_finish_reason="length")

    [task] = run_one_arm(settings, ScriptedArmRunner({"t1": run}), "t1").tasks

    assert task.outcome is TaskOutcome.UNANSWERED_EMPTY


# --- what counts as a completed rollout ---------------------------------------


def test_a_run_whose_trace_file_never_landed_is_infra_never_booked(tmp_path: Path) -> None:
    """An id is not a trace: a run is complete only when its events FILE exists."""
    settings = arm_settings(tmp_path)
    missing = RecordedTrajectory(
        trajectory_id="abc", trace_dir=tmp_path / "traces" / "abc", answer="x", turns=2
    )
    runner = ScriptedArmRunner({"t1": missing})

    summary = run_one_arm(settings, runner, "t1")

    assert summary.tasks == []
    assert summary.excluded == 1
    # Infra is retried once, then excluded — exactly the traceless-raise path.
    assert runner.seen == ["t1", "t1"]


def test_a_traceless_run_at_the_working_directory_is_infra(tmp_path: Path) -> None:
    """``Path()`` — the no-trajectory trace dir — exists; it is still no trace."""
    settings = arm_settings(tmp_path)
    traceless = RecordedTrajectory(trajectory_id="", trace_dir=Path(), answer="", turns=13)

    summary = run_one_arm(settings, ScriptedArmRunner({"t1": traceless}), "t1")

    assert summary.tasks == []
    assert summary.excluded == 1


def test_a_typed_budget_error_raised_with_defaults_stays_infra_through_the_wrapper(
    tmp_path: Path,
) -> None:
    """The real timeout wrapper, a product error carrying no trace: retried, then excluded."""
    from pydocs_eval.optimize.ask_binding import TimeoutBoundedAskRunner
    from pydocs_mcp.harness.core.run_contract import TurnBudgetExceededError

    settings = arm_settings(tmp_path)
    inner = ScriptedArmRunner({"t1": TurnBudgetExceededError(turn_limit=CAP)})
    wrapped = TimeoutBoundedAskRunner(inner=inner, task_timeout_seconds=60.0, max_agent_turns=CAP)

    summary = run_one_arm(settings, wrapped, "t1")

    assert summary.tasks == []
    assert summary.excluded == 1
    assert inner.seen == ["t1", "t1"]
    queue = (Path(settings.out_dir) / "queue.jsonl").read_text(encoding="utf-8")
    assert "infra retry" in queue and "infra excluded" in queue


# --- reading an arm.json written before outcomes existed ----------------------


def test_a_legacy_arm_json_loads_every_row_as_unrecorded(tmp_path: Path) -> None:
    rows = [legacy_row(tmp_path / "t1", answer_chars=47, turns=12)]

    summary = read_arm_summary(write_legacy_arm(tmp_path, "baseline", rows))

    [task] = summary.tasks
    assert task.outcome is TaskOutcome.UNRECORDED
    # Nothing recorded is never read as a measured zero or an empty answer given.
    assert (task.answer, task.tool_calls, task.near_cap) == ("", None, False)
    assert summary.max_agent_turns == UNKNOWN_TURN_BUDGET


def test_an_unknown_outcome_in_arm_json_is_refused_by_name(tmp_path: Path) -> None:
    rows = [{**legacy_row(tmp_path / "t1", answer_chars=47, turns=12), "outcome": "gave_up"}]
    arm_dir = write_legacy_arm(tmp_path, "baseline", rows)

    with pytest.raises(ValueError, match="'gave_up'.*budget_exhausted"):
        read_arm_summary(arm_dir)
