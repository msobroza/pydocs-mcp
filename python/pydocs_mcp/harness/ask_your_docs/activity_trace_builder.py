"""Fold one turn's activity events into a frozen, redacted TurnTrace (PROPOSAL §6).

Redaction happens HERE, once, over ACCUMULATED text — never per delta — so a secret split
across two stream chunks is still masked; only redacted strings ever reach the trace the
page stores. While a round is still streaming, its reasoning is shown only up to the last
whitespace: a word still arriving (perhaps the first half of a key the redactor cannot
recognise yet) is held back until the next delta completes it.

The same builder serves the live path and the after-the-turn path
(:func:`trace_from_messages`), which is the test oracle for the live one.

Example:
    >>> limits = TraceLimits(600, 2000, 40, 20_000, reasoning_display_off=False)
    >>> TraceBuilder(limits=limits, redact=str, scope={}).finish(at=0.5).state
    <TurnState.COMPLETE: 'complete'>
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import Any

from pydocs_mcp.harness.ask_your_docs.activity_events import (
    ActivityEvent,
    ProposedToolCall,
    ReasoningDelta,
    RoundEnded,
    ToolFinished,
    VisionAnalyzed,
    events_from_messages,
)
from pydocs_mcp.harness.ask_your_docs.activity_labels import (
    NOTE_MAX_CHARS,
    clip_label_text,
    tool_step_label,
    vision_step_label,
)
from pydocs_mcp.harness.ask_your_docs.activity_outcomes import (
    citations_from_items,
    envelope_items,
    envelope_meta,
    failure_outcome,
    first_line,
    meta_line,
    meta_notes,
    summarize_tool_result,
    unique_citations,
)
from pydocs_mcp.harness.ask_your_docs.activity_trace import (
    NoteStep,
    StepStatus,
    ThinkingStep,
    ToolStep,
    TraceLimits,
    TurnState,
    TurnStep,
    TurnTrace,
    current_step_label,
)
from pydocs_mcp.harness.ask_your_docs.reasoning_capability import (
    TurnReasoning,
    classify_turn_reasoning,
)
from pydocs_mcp.harness.ask_your_docs.scope_pin import pinned_args

# The agent runs these itself — never through the MCP interceptor, so no scope pin applies.
_AGENT_LOCAL_TOOLS = frozenset({"reinspect_images"})
_UNFINISHED_OUTCOME = "stopped"


class TraceBuilder:
    """Mutable while the turn runs; every :meth:`snapshot` is a fresh frozen TurnTrace."""

    def __init__(
        self, *, limits: TraceLimits, redact: Callable[[str], str], scope: Mapping[str, str]
    ) -> None:
        self._limits, self._redact, self._scope = limits, redact, dict(scope)
        self._steps: list[TurnStep] = []
        self._tool_at: dict[str, int] = {}  # call id -> index in _steps
        self._tool_args: dict[str, Mapping[str, Any]] = {}  # call id -> the model's args
        self._raw_reasoning: dict[int, str] = {}  # index -> UNREDACTED text, never stored
        self._open_thinking: int | None = None
        self._tokens: dict[str, int] = {}
        self._reasoning_tokens: int | None = None
        self._redacted = False
        self._model: str | None = None
        self._answered = False

    def add_note(self, text: str | None, kind: str) -> None:
        """A note in words (rephrase, scope); ``None`` adds nothing."""
        if text:
            self._steps.append(NoteStep(self._redact(text), kind))

    def apply(self, event: ActivityEvent) -> None:
        """Fold one event in."""
        match event:
            case ReasoningDelta():
                self._reasoning_delta(event)
            case RoundEnded():
                self._round_ended(event)
            case ToolFinished():
                self._tool_finished(event)
            case VisionAnalyzed():
                facts = first_line(self._redact(event.facts))
                self._steps.append(
                    NoteStep(f"{vision_step_label(running=False)}: {facts}", "vision")
                )

    def snapshot(self, at: float | None = None) -> TurnTrace:
        """The turn so far (state RUNNING); ``at`` = seconds since the turn began."""
        return self._trace(TurnState.RUNNING, at)

    def finish(self, at: float | None) -> TurnTrace:
        """The finished turn."""
        return self._ended(TurnState.COMPLETE, at)

    def stop(self, at: float | None) -> TurnTrace:
        """The turn the user stopped (or a rerun / page release cut short)."""
        return self._ended(TurnState.STOPPED, at)

    def fail(self, caption: str, *, reason: str, at: float | None) -> TurnTrace:
        """The turn a model / network / auth error stopped; ``caption`` gets redacted here."""
        trace = self._ended(TurnState.ERROR, at)
        return replace(trace, failure=self._redact(caption), failure_reason=reason)

    # ── events ──

    def _reasoning_delta(self, event: ReasoningDelta) -> None:
        self._redacted = self._redacted or event.redacted
        if self._limits.reasoning_display_off or not event.text:
            return
        index = self._thinking_index(event.at)
        self._raw_reasoning[index] += event.text

    def _round_ended(self, event: RoundEnded) -> None:
        self._redacted = self._redacted or event.redacted
        self._count_usage(event)
        self._close_thinking(event.reasoning, event.at)
        narration = event.text.strip()
        if narration and event.tool_calls and not event.reasoning:  # non-reasoning models only
            self.add_note(clip_label_text(narration, NOTE_MAX_CHARS), "narration")
        for call in event.tool_calls:
            self._start_tool(call, event.at)
        self._answered = self._answered or not event.tool_calls

    def _tool_finished(self, event: ToolFinished) -> None:
        if event.call_id not in self._tool_at:  # a result whose call we never saw proposed
            self._start_tool(ProposedToolCall(event.call_id, event.name, {}), event.at)
        index, text = self._tool_at[event.call_id], self._redact(event.text)
        args, meta = self._tool_args[event.call_id], envelope_meta(event.structured)
        outcome = summarize_tool_result(event.name, args, event.structured, text)
        rows = citations_from_items(envelope_items(event.structured))
        self._steps[index] = replace(
            self._steps[index],
            status=StepStatus.FAILED if event.failed else StepStatus.OK,
            ended_at=event.at,
            outcome=failure_outcome(text) if event.failed else self._redact(outcome),
            notes=tuple(self._redact(note) for note in meta_notes(meta)),
            citations=tuple(citation.redacted(self._redact) for citation in rows),
            meta_line=self._redact(meta_line(meta)),
            preview=text[: self._limits.result_preview_chars],
            result_chars=len(event.text),
        )

    def _start_tool(self, call: ProposedToolCall, at: float | None) -> None:
        local = call.name in _AGENT_LOCAL_TOOLS
        sent = dict(call.args) if local else pinned_args(call.name, call.args, self._scope)
        pinned = tuple(sorted(k for k in sent if k not in call.args or call.args[k] != sent[k]))
        self._tool_at[call.call_id], self._tool_args[call.call_id] = len(self._steps), call.args
        self._steps.append(
            ToolStep(
                call_id=call.call_id,
                name=call.name,
                label=self._redact(tool_step_label(call.name, call.args, running=False)),
                running_label=self._redact(tool_step_label(call.name, call.args, running=True)),
                status=StepStatus.RUNNING,
                started_at=at,
                model_args=self._args_json(call.args),
                sent_args=self._args_json(sent),
                pinned_keys=pinned,
            )
        )

    def _args_json(self, args: Mapping[str, Any]) -> str:
        raw = json.dumps(dict(args), ensure_ascii=False, sort_keys=True, default=str)
        return clip_label_text(self._redact(raw), self._limits.args_max_chars)

    def _thinking_index(self, at: float | None) -> int:
        if self._open_thinking is None:
            self._open_thinking = len(self._steps)
            self._raw_reasoning[self._open_thinking] = ""
            self._steps.append(ThinkingStep("", started_at=at))
        return self._open_thinking

    def _close_thinking(self, reasoning: str, at: float | None) -> None:
        if reasoning and not self._limits.reasoning_display_off:
            self._raw_reasoning[self._thinking_index(at)] = reasoning  # the whole round's text
        if self._open_thinking is not None:
            step = self._steps[self._open_thinking]
            self._steps[self._open_thinking] = replace(step, ended_at=at, finished=True)
        self._open_thinking = None

    def _count_usage(self, event: RoundEnded) -> None:
        self._model = event.model or self._model
        if event.usage is None:
            return
        for key in ("input_tokens", "output_tokens"):
            self._tokens[key] = self._tokens.get(key, 0) + getattr(event.usage, key)
        if event.usage.reasoning_tokens is not None:
            self._reasoning_tokens = (self._reasoning_tokens or 0) + event.usage.reasoning_tokens

    # ── snapshots ──

    def _ended(self, state: TurnState, at: float | None) -> TurnTrace:
        self._close_thinking("", at)
        for index, step in enumerate(self._steps):
            if isinstance(step, ToolStep) and step.status is StepStatus.RUNNING:
                self._steps[index] = replace(
                    step, status=StepStatus.FAILED, outcome=_UNFINISHED_OUTCOME
                )
        return self._trace(state, at)

    def _trace(self, state: TurnState, at: float | None) -> TurnTrace:
        steps = self._rendered_steps()
        tools = [step for step in steps if isinstance(step, ToolStep)]
        shown = self._limits.max_steps_shown
        return TurnTrace(
            state=state,
            steps=tuple(steps[:shown]),
            step_count=sum(not isinstance(step, NoteStep) for step in steps),
            tool_count=len(tools),
            failed_count=sum(step.status is StepStatus.FAILED for step in tools),
            hidden_steps=max(0, len(steps) - shown),
            elapsed_s=at,
            citations=unique_citations(c for step in tools for c in step.citations),
            reasoning=self._turn_reasoning(),
            reasoning_tokens=self._reasoning_tokens,
            input_tokens=self._tokens.get("input_tokens"),
            output_tokens=self._tokens.get("output_tokens"),
            model=self._model,
            answered=self._answered,
            current=current_step_label(steps),
        )

    def _turn_reasoning(self) -> TurnReasoning:
        return classify_turn_reasoning(
            text_chars=sum(len(text) for text in self._raw_reasoning.values()),
            reasoning_tokens=self._reasoning_tokens,
            redacted=self._redacted,
            display_off=self._limits.reasoning_display_off,
        )

    def _rendered_steps(self) -> list[TurnStep]:
        budget, rendered = self._limits.reasoning_max_chars, []
        for index, step in enumerate(self._steps):
            if isinstance(step, ThinkingStep):
                step, budget = self._rendered_thinking(index, step, budget)
            rendered.append(step)
        return rendered

    def _rendered_thinking(
        self, index: int, step: ThinkingStep, budget: int
    ) -> tuple[ThinkingStep, int]:
        text = self._redact(self._raw_reasoning.get(index, ""))
        text = text if step.finished else _whole_words(text)
        capped, clipped = _head_and_tail(text, budget)
        return replace(step, text=capped, clipped=clipped), max(0, budget - len(text))


def trace_from_messages(messages: Iterable[Any], builder: TraceBuilder) -> TurnTrace:
    """The trace of a finished turn's messages — the ``live: false`` path and the oracle."""
    for event in events_from_messages(messages):
        builder.apply(event)
    return builder.finish(at=None)


def _whole_words(text: str) -> str:
    """``text`` up to its last whitespace; a word still arriving is held back."""
    return text[: max(text.rfind(space) for space in " \n\t") + 1]


def _head_and_tail(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    if limit <= 0:
        return "", True
    head, tail = limit - limit // 2, limit // 2
    omitted = len(text) - head - tail
    return f"{text[:head]}\n[… {omitted:,} characters omitted …]\n{text[len(text) - tail :]}", True


__all__ = ("TraceBuilder", "trace_from_messages")
