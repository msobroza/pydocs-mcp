"""What the example-needle-chat repro runner records per question.

The runner (``benchmarks/tools/run_example_needle_chat_repro.py``) asks the chat
slice's questions through the chat page's own agent. One question becomes a
``ChatQuestionRun``, which satisfies the campaign's ``RecordedRun`` — so
``before_after_arm.record_of`` writes its ``arm.json`` row exactly as it writes a
campaign arm's, and ``read_arm_summary``, ``measure_arm`` and ``--report-only`` read
the runner's output unchanged. What the runner adds per question — turns, calls by
tool, tokens, the outcome and the seven behaviour counts (``chat_behaviour``) — is
merged into the chat trace writer's own ``question.json``.

Langchain-free and product-free at import, like every campaign module.

Example:
    >>> run = ChatQuestionRun(task, trajectory_id, tuple(messages), question_dir, 4.2)
    >>> merge_question_record(run.question_dir, question_record(run, settings))  # doctest: +SKIP
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydocs_eval.campaign.before_after_arm import ArmSettings, outcome_of_run
from pydocs_eval.campaign.chat_behaviour import (
    behaviour_counts,
    calls_by_tool,
    message_text,
    model_turns,
    observed_calls,
)
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.trajectory.ask_outcome import TaskOutcome, is_budget_exhausted_answer
from pydocs_eval.trajectory.server_capture import (
    SERVER_EVENTS_FILENAME,
    read_server_capture,
    trace_recorded,
)


@dataclass(frozen=True, slots=True)
class ChatQuestionRun:
    """One question the runner asked — a ``RecordedRun`` the campaign's row builder reads."""

    task: EvalTask
    trajectory_id: str
    messages: tuple[Any, ...]
    question_dir: Path
    seconds: float
    error: str | None = None
    # A graph that raises GraphRecursionError at the cap (vision_subagent) spends its
    # budget without the canned apology; ``run_evidence`` reads this flag.
    budget_exhausted: bool = False

    @property
    def trace_dir(self) -> Path:
        return self.question_dir.resolve()

    @property
    def answer(self) -> str:
        """The final reply's text; ``""`` when the run ended on a reply still calling tools."""
        last = self.messages[-1] if self.messages else None
        if last is None or getattr(last, "type", "") != "ai" or getattr(last, "tool_calls", None):
            return ""
        return message_text(last.content)

    @property
    def turns(self) -> int:
        """Model replies, by the product's ONE turn rule — the rule ``Trajectory.turns`` uses,
        so each arm counts turns as its own product commit does."""
        from pydocs_mcp.harness.ask_your_docs.binding_trajectory import model_reply_count

        return model_reply_count(self.messages)

    @property
    def wall_seconds(self) -> float:
        return self.seconds

    def server_tool_calls(self) -> tuple[object, ...]:
        """The calls the server recorded for this question; empty without a recorded trace."""
        if not trace_recorded(self.trajectory_id, self.trace_dir):
            return ()
        return read_server_capture(self.trace_dir / SERVER_EVENTS_FILENAME).tool_events


def question_record(run: ChatQuestionRun, settings: ArmSettings) -> dict[str, object]:
    """The runner's fields for ``question.json`` — beside the trace writer's own keys."""
    calls = observed_calls(run.messages)
    turns = model_turns(run.messages)
    return {
        "n_model_turns": run.turns,
        "n_tool_calls": len(calls),
        "calls_by_tool": calls_by_tool(calls),
        "last_reply_has_tool_calls": bool(turns and getattr(turns[-1], "tool_calls", None)),
        "seconds": run.seconds,
        **_token_totals(turns),
        "outcome": outcome_of_run(run, settings),
        "sentinel_seen": is_budget_exhausted_answer(run.answer),
        "error": run.error,
        **behaviour_counts(calls),
    }


def merge_question_record(question_dir: Path, record: Mapping[str, object]) -> Path:
    """Add the runner's ``record`` to the trace writer's ``question.json``.

    The writer's own keys — the question, its rewrite, the answer and ``finalized`` —
    are never overwritten: the runner only adds what the writer does not record.
    """
    from pydocs_mcp.harness.ask_your_docs.chat_trace import QUESTION_RECORD_FILENAME

    path = question_dir / QUESTION_RECORD_FILENAME
    written = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    added = {key: _json_value(value) for key, value in record.items()}
    # The writer's own serialization, so a merged file reads like an unmerged one.
    text = json.dumps(
        {**added, **written}, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    path.write_text(text, encoding="utf-8")
    return path


def _token_totals(turns: Iterable[Any]) -> dict[str, int]:
    usages = [getattr(turn, "usage_metadata", None) or {} for turn in turns]
    return {
        "input_tokens": sum(int(u.get("input_tokens") or 0) for u in usages),
        "output_tokens": sum(int(u.get("output_tokens") or 0) for u in usages),
    }


def _json_value(value: object) -> object:
    """StrEnum members serialize as their value; everything else is already JSON."""
    return str(value) if isinstance(value, TaskOutcome) else value


__all__ = (
    "ChatQuestionRun",
    "merge_question_record",
    "question_record",
)
