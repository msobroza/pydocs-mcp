"""What the chat repro runner records per question, and the arm row the campaign writes for it.

``ChatQuestionRun`` satisfies the campaign's ``RecordedRun``, so its ``arm.json`` row
comes from ``before_after_arm.record_of`` — the same builder a campaign arm uses. Fed
real langchain messages, never a live model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage, HumanMessage

from pydocs_eval.campaign.before_after_arm import ArmSettings, record_of
from pydocs_eval.campaign.chat_repro import (
    ChatQuestionRun,
    merge_question_record,
    question_record,
)
from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.trajectory.ask_outcome import ASK_BUDGET_EXHAUSTED_REPLY, TaskOutcome
from tests.trajectory._ask_traces import write_ask_trajectory

from ._chat_messages import FINAL_ANSWER, STRATEGIES, call, conversation, turn

_TASK = EvalTask(
    task_id="example-needle-chat/q00",
    query="Where is the MaxSim score computed?",
    gold=GoldAnswer(file_set=(STRATEGIES,), extra={"symbol_0": "maxsim"}),
    corpus_source=Path,
)


def _settings(max_agent_turns: int = 12) -> ArmSettings:
    return ArmSettings(
        role="baseline",
        commit="c" * 40,
        workspace="/ws",
        model="qwen/qwen3.8-27b",
        trace_root="/traces",
        out_dir="/out",
        max_agent_turns=max_agent_turns,
        estimated_usd_per_rollout=0.0,
        cost_ceiling_usd=0.0,
    )


def _run(tmp_path: Path, messages: list, **overrides: object) -> ChatQuestionRun:
    question_dir = tmp_path / "trace" / "questions" / "1"
    question_dir.mkdir(parents=True, exist_ok=True)
    fields = {"trajectory_id": "t" * 32, "question_dir": question_dir, "seconds": 3.25}
    return ChatQuestionRun(task=_TASK, messages=tuple(messages), **{**fields, **overrides})


def test_the_question_record_carries_every_key(tmp_path: Path) -> None:
    record = question_record(_run(tmp_path, conversation()), _settings())

    assert record["n_model_turns"] == 5, "the seeded proposal is not a model turn"
    assert record["n_tool_calls"] == 6
    assert record["calls_by_tool"] == {
        "get_overview": 1,
        "search_codebase": 1,
        "get_symbol": 2,
        "read_file": 1,
        "grep": 1,
    }
    assert record["last_reply_has_tool_calls"] is False
    assert record["seconds"] == 3.25
    assert (record["input_tokens"], record["output_tokens"]) == (900, 80)
    assert record["outcome"] == TaskOutcome.ANSWERED
    assert record["sentinel_seen"] is False
    assert record["error"] is None
    assert record["get_overview_calls"] == 1


def test_the_canned_apology_is_seen_and_never_kept_as_the_answer(tmp_path: Path) -> None:
    run = _run(tmp_path, [HumanMessage(content="q"), AIMessage(content=ASK_BUDGET_EXHAUSTED_REPLY)])

    record = question_record(run, _settings())
    row = record_of(run.task, run, _settings())

    assert (record["sentinel_seen"], record["outcome"]) == (True, TaskOutcome.BUDGET_EXHAUSTED)
    assert (row.answer, row.answer_chars, row.outcome) == ("", 0, TaskOutcome.BUDGET_EXHAUSTED)


def test_a_graph_that_raised_at_the_cap_reads_as_budget_exhausted(tmp_path: Path) -> None:
    """vision_subagent raises GraphRecursionError at the cap instead of the apology."""
    messages = [HumanMessage(content="q"), turn(call("a", "grep", pattern="x"))]
    run = _run(tmp_path, messages, budget_exhausted=True)

    assert question_record(run, _settings())["outcome"] == TaskOutcome.BUDGET_EXHAUSTED


def test_a_reply_still_calling_tools_is_unanswered(tmp_path: Path) -> None:
    messages = [HumanMessage(content="q"), turn(call("a", "grep", pattern="x"))]

    record = question_record(_run(tmp_path, messages), _settings())

    assert record["last_reply_has_tool_calls"] is True
    assert record["outcome"] == TaskOutcome.UNANSWERED_EMPTY


def test_the_record_never_overwrites_the_trace_writers_keys(tmp_path: Path) -> None:
    run = _run(tmp_path, conversation())
    written = {
        "schema_version": 1,
        "question": "typed",
        "standalone_question": "typed",
        "answer": "the writer's answer",
        "finalized": False,
    }
    (run.question_dir / "question.json").write_text(json.dumps(written), encoding="utf-8")
    record = {**question_record(run, _settings()), "answer": "not mine to write"}

    merge_question_record(run.question_dir, record)

    merged = json.loads((run.question_dir / "question.json").read_text(encoding="utf-8"))
    assert {key: merged[key] for key in written} == written
    assert merged["n_model_turns"] == 5 and merged["outcome"] == "answered"


def test_the_calls_are_read_back_only_under_the_campaigns_trace_rule(tmp_path: Path) -> None:
    """``trace_recorded`` — an id AND the events file — the rule an arm books a run by."""
    question_dir = write_ask_trajectory(tmp_path / "trace", calls=[("grep", {"pattern": "x"}, 1)])

    named = _run(tmp_path, conversation(), question_dir=question_dir)
    unnamed = _run(tmp_path, conversation(), question_dir=question_dir, trajectory_id="")

    assert len(named.server_tool_calls()) == 1
    assert unnamed.server_tool_calls() == (), "a capture on disk is no trace without an id"


def test_the_campaigns_row_builder_reads_the_run_as_a_recorded_run(tmp_path: Path) -> None:
    run = _run(tmp_path, conversation())

    row = record_of(run.task, run, _settings(max_agent_turns=6))

    assert row.task_id == "example-needle-chat/q00"
    assert row.trace_dir == str(run.question_dir.resolve())
    assert row.trajectory_id == "t" * 32
    assert row.gold_files == [STRATEGIES]
    assert (row.turns, row.wall_seconds) == (5, 3.25)
    assert (row.answer, row.answer_chars) == (FINAL_ANSWER, len(FINAL_ANSWER))
    assert row.near_cap is True, "5 turns of a 6-turn budget"
    assert row.outcome == TaskOutcome.ANSWERED
