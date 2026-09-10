"""One assistant turn's activity, as the page stores it (PROPOSAL §2, §6).

A :class:`TurnTrace` is plain frozen data — every string already redacted and capped by
``activity_trace_builder`` — so the page can keep one per turn in session state and redraw
it on every rerun with no network and no live objects. :func:`turn_summary_label` is the
panel's always-visible L0 line; :func:`trim_history` compacts turns older than
``ask_your_docs.ui.activity.history_keep`` to that line plus their sources.

Example:
    >>> trace = TurnTrace(state=TurnState.COMPLETE, elapsed_s=1.1)
    >>> turn_summary_label(trace)
    'Answered without searching · 1.1 s'
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from pydocs_mcp.harness.ask_your_docs.activity_outcomes import Citation
from pydocs_mcp.harness.ask_your_docs.reasoning_capability import TurnReasoning

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config.ask_your_docs_ui_models import AskYourDocsUiConfig

_REASONING_SUFFIX = {
    TurnReasoning.SHOWN: "reasoning shown",
    TurnReasoning.HIDDEN: "reasoning hidden",
}


class StepStatus(StrEnum):
    """Where one tool call stands; every state is also spelled out in words by the view."""

    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"


class TurnState(StrEnum):
    """The turn as a whole — a failed tool leaves it ``COMPLETE``; a failed model call doesn't."""

    RUNNING = "running"
    COMPLETE = "complete"
    ERROR = "error"
    STOPPED = "stopped"  # Stop pressed, or a rerun / page release mid-turn


@dataclass(frozen=True, slots=True)
class NoteStep:
    """A line in words between steps: ``rephrase``, ``scope``, ``vision`` or ``narration``."""

    text: str
    kind: str


@dataclass(frozen=True, slots=True)
class ThinkingStep:
    """One model round's reasoning (plain text, head + tail kept within the turn's cap)."""

    text: str
    started_at: float | None
    ended_at: float | None = None
    finished: bool = False  # False while the round is still streaming
    clipped: bool = False


@dataclass(frozen=True, slots=True)
class ToolStep:
    """One tool call: its labels and outcome (L1) and its technical details (L2)."""

    call_id: str
    name: str
    label: str  # done form: "Found callers of X"
    running_label: str  # "Finding callers of X …"
    status: StepStatus
    started_at: float | None
    ended_at: float | None = None
    model_args: str = ""  # JSON the model proposed
    sent_args: str = ""  # JSON actually sent, after the scope pin
    pinned_keys: tuple[str, ...] = ()
    outcome: str = ""
    notes: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()
    meta_line: str = ""
    preview: str = ""  # head of the raw result
    result_chars: int = 0

    @property
    def duration_s(self) -> float | None:
        """Seconds the call took; None when the turn was built after the fact."""
        if self.started_at is None or self.ended_at is None:
            return None
        return self.ended_at - self.started_at


TurnStep = NoteStep | ThinkingStep | ToolStep


@dataclass(frozen=True, slots=True)
class TurnTrace:
    """Everything one turn's panel shows; counts cover hidden steps too."""

    state: TurnState
    steps: tuple[TurnStep, ...] = ()
    step_count: int = 0  # thinking + tool steps
    tool_count: int = 0
    failed_count: int = 0
    hidden_steps: int = 0  # beyond max_steps_shown: "+N more steps"
    elapsed_s: float | None = None
    citations: tuple[Citation, ...] = ()
    reasoning: TurnReasoning = TurnReasoning.UNKNOWN
    reasoning_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None
    answered: bool = False  # a final round with no tool calls arrived
    current: str = ""  # while running: the label of the step in progress
    failure: str = ""  # redacted caption of the error that stopped the turn
    failure_reason: str = ""  # that error, in words
    compact: bool = False  # an old turn: summary line and sources only

    @property
    def file_count(self) -> int:
        """Distinct files the turn's sources point at (a file cited twice counts once)."""
        return len({citation.path for citation in self.citations})


@dataclass(frozen=True, slots=True)
class TraceLimits:
    """The caps one trace honours; built from ``ask_your_docs.ui`` (the single source)."""

    result_preview_chars: int
    args_max_chars: int
    max_steps_shown: int
    reasoning_max_chars: int
    reasoning_display_off: bool

    @classmethod
    def from_ui_config(cls, ui: AskYourDocsUiConfig) -> TraceLimits:
        """Limits from the YAML block; ``display: hidden`` or ``capture: false`` hide reasoning."""
        activity, reasoning = ui.activity, ui.reasoning
        return cls(
            result_preview_chars=activity.result_preview_chars,
            args_max_chars=activity.args_max_chars,
            max_steps_shown=activity.max_steps_shown,
            reasoning_max_chars=reasoning.max_chars,
            reasoning_display_off=reasoning.display == "hidden" or not reasoning.capture,
        )


def current_step_label(steps: Sequence[TurnStep]) -> str:
    """While running: the step in progress — a tool's running label, or "Thinking …"."""
    running = [s for s in steps if isinstance(s, ToolStep) and s.status is StepStatus.RUNNING]
    if running:
        return running[-1].running_label
    open_thinking = any(isinstance(s, ThinkingStep) and not s.finished for s in steps)
    return "Thinking …" if open_thinking else ""


def writing_the_answer(trace: TurnTrace) -> bool:
    """While running: every tool call returned, so the model is on its next round.

    WHY this proxy: v1 does not stream the answer, and a round is only known to be the final
    one when it ends; a round that calls another tool clears it again."""
    tools = [step for step in trace.steps if isinstance(step, ToolStep)]
    return bool(tools) and all(step.status is not StepStatus.RUNNING for step in tools)


def turn_summary_label(trace: TurnTrace) -> str:
    """The L0 line, e.g. "Done in 6.4 s · 4 steps · 3 files · reasoning shown"."""
    elapsed = f"{trace.elapsed_s:.1f} s" if trace.elapsed_s is not None else ""
    steps = _plural(trace.step_count, "step")
    if trace.state is TurnState.RUNNING:
        return trace.current or "Working …"
    if trace.state is TurnState.STOPPED:
        return _joined(f"Stopped by you after {steps}", elapsed)
    if trace.state is TurnState.ERROR:
        return _joined(f"Stopped after {steps}", elapsed, trace.failure_reason)
    if trace.tool_count == 0:
        return _joined("Answered without searching", elapsed)
    return _joined(f"Done in {elapsed}" if elapsed else "Done", steps, *_done_details(trace))


def _done_details(trace: TurnTrace) -> tuple[str, ...]:
    failed = f"{trace.failed_count} failed" if trace.failed_count else ""
    files = trace.file_count
    return (
        failed,
        _plural(files, "file") if files else "",
        _REASONING_SUFFIX.get(trace.reasoning, ""),
    )


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _joined(*parts: str) -> str:
    return " · ".join(part for part in parts if part)


def trim_history(traces: Mapping[int, TurnTrace], keep: int) -> dict[int, TurnTrace]:
    """The newest ``keep`` turns (by message index) whole; older ones compacted."""
    recent = set(sorted(traces)[-keep:]) if keep > 0 else set()
    return {
        index: trace if index in recent else compact_trace(trace) for index, trace in traces.items()
    }


def compact_trace(trace: TurnTrace) -> TurnTrace:
    """``trace`` without its steps: the summary line and the sources survive."""
    return replace(trace, steps=(), hidden_steps=0, compact=True)


def turn_activity_record(trace: TurnTrace) -> dict[str, object]:
    """The one ``turn_activity`` log record per turn: counts and states, never content."""
    return {
        "event": "turn_activity",
        "state": str(trace.state),
        "steps": trace.step_count,
        "tools": trace.tool_count,
        "failed": trace.failed_count,
        "files": trace.file_count,
        "reasoning": str(trace.reasoning),
        "elapsed_s": round(trace.elapsed_s, 1) if trace.elapsed_s is not None else None,
    }


__all__ = (
    "NoteStep",
    "StepStatus",
    "ThinkingStep",
    "ToolStep",
    "TraceLimits",
    "TurnState",
    "TurnStep",
    "TurnTrace",
    "compact_trace",
    "current_step_label",
    "trim_history",
    "turn_activity_record",
    "turn_summary_label",
    "writing_the_answer",
)
