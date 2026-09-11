"""The activity panel on the chat page: live while a turn runs, redrawn from its trace after.

Each assistant turn gets one ``st.status`` above its answer (PROPOSAL §1–§2): the label is
the always-visible summary (L0), the body lists the steps in words (L1), and a per-step
"Details" expander (L2) appears only under the session's "Show technical details" toggle.

Threading rule: only the script thread touches widgets. The agent runs on the page's loop
and reports through :attr:`LiveActivityPanel.sink` (``queue.Queue.put_nowait``); the
script thread drains the queue and repaints, at most every ``_REPAINT_S``. Every string
shown comes from a redacted :class:`TurnTrace` and untrusted text is rendered as plain
text (``st.text`` / code spans) or markdown-escaped — never as markdown or HTML. A step's
Material icon is prepended only after that escaping (``activity_markdown``).

Example:
    panel = LiveActivityPanel(builder, settings, "t3", on_stopped=save)
    answer = panel.drain(asyncio.run_coroutine_threadsafe(turn(panel.sink), loop))
    trace = panel.finish()
"""

from __future__ import annotations

import concurrent.futures
import queue
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.activity_events import ActivityEvent
from pydocs_mcp.harness.ask_your_docs.activity_labels import (
    NOTE_ICONS,
    THINKING_ICON,
    failure_reason,
)
from pydocs_mcp.harness.ask_your_docs.activity_markdown import (
    iconed_markdown,
    plain_markdown,
    thinking_teaser_markdown,
    tool_step_markdown,
)
from pydocs_mcp.harness.ask_your_docs.activity_outcomes import Citation, split_cited
from pydocs_mcp.harness.ask_your_docs.activity_trace import (
    NoteStep,
    StepStatus,
    ThinkingStep,
    ToolStep,
    TurnState,
    TurnStep,
    TurnTrace,
    turn_summary_label,
    writing_the_answer,
)
from pydocs_mcp.harness.ask_your_docs.activity_trace_builder import TraceBuilder
from pydocs_mcp.harness.ask_your_docs.reasoning_capability import reasoning_turn_sentence
from pydocs_mcp.retrieval.config.ask_your_docs_ui_models import AskYourDocsUiConfig

_POLL_S = 0.05  # how long the script thread waits for the next event
_REPAINT_S = 0.1  # the step list repaints at most 10 times a second
_LABEL_TICK_S = 1.0  # the running label's elapsed seconds refresh at most once a second
_LIVE_THINKING_LINES = 12
_CHIPS_PER_STEP = 3
_DISCLAIMER = "Model's working notes. They may be incomplete or differ from what it actually did."
_WRITING_THE_ANSWER = "Writing the answer…"
_NOTE_PREFIX = {"rephrase": "✓ ", "narration": "Note: "}

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class PanelNote:
    """A line in words the page's own turn code adds (rephrase, scope) — not a graph event."""

    text: str | None
    kind: str


PanelItem = ActivityEvent | PanelNote  # what the agent's loop hands the panel
PanelSink = Callable[[PanelItem], None]


@dataclass(frozen=True, slots=True)
class PanelSettings:
    """What the panel shows beyond the trace: the YAML block, the session toggle, the host."""

    ui: AskYourDocsUiConfig
    technical: bool
    host: str


def panel_expanded(trace: TurnTrace, ui: AskYourDocsUiConfig) -> bool:
    """Does this turn's panel open expanded? The ONE rule both render paths share.

    WHY it is shared: the live path and the rerun path each decided this for themselves, and
    the rerun path ignored ``collapse_when_done`` entirely — so a finished turn re-collapsed
    on the next rerun however the setting was configured, and the panel read as missing.
    A turn that failed or was stopped always stays open; only a COMPLETE one may close.
    """
    return trace.state is not TurnState.COMPLETE or not ui.activity.collapse_when_done


class LiveActivityPanel:
    """One running turn's panel; script thread only — the agent reports through ``sink``."""

    def __init__(
        self,
        builder: TraceBuilder,
        settings: PanelSettings,
        key_prefix: str,
        on_stopped: Callable[[TurnTrace], object],
    ) -> None:
        self._builder, self._settings, self._key = builder, settings, key_prefix
        self._on_stopped = on_stopped
        self._events: queue.Queue[PanelItem] = queue.Queue()
        self._started = time.perf_counter()
        self._painted_at, self._dirty = 0.0, False
        self._label, self._label_at = "", 0.0
        self._status = st.status("Working …", expanded=True, state="running")
        self._body = self._status.empty()
        self._writing = st.empty()  # below the panel: "Writing the answer…" (PROPOSAL §2)

    @property
    def sink(self) -> PanelSink:
        """Thread-safe: the agent's loop hands every event and note to this."""
        return self._events.put_nowait

    def drain(self, future: concurrent.futures.Future[_T]) -> _T:
        """Repaint from events until the turn is done; its result, or its exception."""
        try:
            while not (future.done() and self._events.empty()):
                self._take_next()
            return future.result()
        except BaseException as exc:
            if not isinstance(exc, Exception):  # Stop / a rerun: Streamlit's control flow
                self._stopped()
            raise
        finally:
            future.cancel()  # a turn the script stopped waiting for must not keep running

    def finish(self) -> TurnTrace:
        """The finished turn, drawn in its final state."""
        return self._show_final(self._builder.finish(self._elapsed()))

    def fail(self, caption: str, error_class: str, *, released: bool) -> TurnTrace:
        """The turn an error stopped; after the page released its agent, it reads as stopped."""
        at = self._elapsed()
        if released:
            return self._show_final(self._builder.stop(at))
        reason = failure_reason(error_class)
        return self._show_final(self._builder.fail(caption, reason=reason, at=at))

    @property
    def settings(self) -> PanelSettings:
        """The settings this panel draws with (the page reads ``ui.activity`` from them)."""
        return self._settings

    def _stopped(self) -> None:
        # Saved BEFORE drawing: the page is being torn down, and a draw that is itself
        # interrupted must not lose the turn — the next run redraws it from the trace.
        trace = self._builder.stop(self._elapsed())
        self._on_stopped(trace)
        self._show_final(trace)

    def _elapsed(self) -> float:
        return time.perf_counter() - self._started

    def _take_next(self) -> None:
        try:
            item = self._events.get(timeout=_POLL_S)
        except queue.Empty:
            self._repaint()
            return
        if isinstance(item, PanelNote):
            self._builder.add_note(item.text, item.kind)
        else:
            self._builder.apply(item)
        self._dirty = True
        self._repaint()

    def _repaint(self) -> None:
        now = time.perf_counter()
        if self._dirty and now - self._painted_at >= _REPAINT_S:
            snapshot = self._builder.snapshot(self._elapsed())
            with self._body.container():
                _render_live_steps(snapshot)
            self._show_writing(writing_the_answer(snapshot))
            self._painted_at, self._dirty = now, False
            self._relabel(turn_summary_label(snapshot), now)
        elif now - self._label_at >= _LABEL_TICK_S:
            self._relabel(self._label, now)

    def _relabel(self, label: str, now: float) -> None:
        if label == self._label and now - self._label_at < _LABEL_TICK_S:
            return
        self._label, self._label_at = label, now
        self._status.update(
            label=plain_markdown(f"{label or 'Working …'} · {self._elapsed():.1f} s")
        )

    def _show_writing(self, writing: bool) -> None:
        if writing:
            self._writing.caption(_WRITING_THE_ANSWER)
        else:
            self._writing.empty()

    def _show_final(self, trace: TurnTrace) -> TurnTrace:
        self._show_writing(False)  # the answer (or the error) takes its place
        expanded = panel_expanded(trace, self._settings.ui)
        label = plain_markdown(turn_summary_label(trace))
        self._status.update(label=label, state=status_state(trace), expanded=expanded)
        with self._body.container():
            render_trace_body(trace, self._settings, self._key)
        return trace


# ── shared rendering ──


def status_state(trace: TurnTrace) -> str:
    """``st.status``'s state: only a turn that finished is "complete"; stopped reads as error."""
    if trace.state is TurnState.RUNNING:
        return "running"
    return "complete" if trace.state is TurnState.COMPLETE else "error"


def render_saved_turn(
    trace: TurnTrace, answer: str, question: str, settings: PanelSettings, key_prefix: str
) -> None:
    """A past assistant turn on rerun: its panel in its final state, the answer, the footer."""
    label = plain_markdown(turn_summary_label(trace))
    expanded = panel_expanded(trace, settings.ui)
    with st.status(label, state=status_state(trace), expanded=expanded):  # type: ignore[arg-type]
        render_trace_body(trace, settings, key_prefix)
    if answer:
        st.markdown(answer)
    render_turn_footer(trace, answer, question, key_prefix)


def render_turn_footer(trace: TurnTrace, answer: str, question: str, key_prefix: str) -> None:
    """Below the answer: the failure (when the turn failed), then the Sources row."""
    if trace.state is TurnState.ERROR:  # provider / MCP text: an image or link in it stays inert
        st.error(plain_markdown(trace.failure))
        st.caption(plain_markdown(f'Your question was not answered: "{question}"'))
    render_sources(trace.citations, answer, key_prefix)


def render_sources(citations: Sequence[Citation], answer: str, key_prefix: str) -> None:
    """Sources the answer names, then the rest under "Also looked at (N)"."""
    cited, other = split_cited(citations, answer)
    if cited:
        st.caption(f"Sources  {_chips(cited)}")
    if other:
        with st.expander(f"Also looked at ({len(other)})", key=f"ayd-also-{key_prefix}"):
            st.caption(_chips(other))


def render_trace_body(trace: TurnTrace, settings: PanelSettings, key_prefix: str) -> None:
    """The finished panel's body (L1, plus L2 under the technical toggle)."""
    if trace.compact:
        st.caption("Steps of older turns are not kept; the sources are.")
        return
    if any(isinstance(step, ThinkingStep) and step.text for step in trace.steps):
        st.caption(_DISCLAIMER)
    for index, step in enumerate(trace.steps):
        _render_final_step(step, settings, f"{key_prefix}-{index}")
    for line in _closing_lines(trace, settings.host):
        st.caption(plain_markdown(line))


def _closing_lines(trace: TurnTrace, host: str) -> list[str]:
    more = [f"+{trace.hidden_steps} more steps"] if trace.hidden_steps else []
    tokens = trace.reasoning_tokens
    sentence = reasoning_turn_sentence(trace.reasoning, reasoning_tokens=tokens, host=host)
    return [*more, *filter(None, [sentence, usage_line(trace)])]


def usage_line(trace: TurnTrace) -> str:
    """ "Tokens: 3.1k in · 612 out (412 reasoning) · model" — only when usage was reported."""
    if trace.input_tokens is None or trace.output_tokens is None:
        return ""
    tokens_in, tokens_out = _token_count(trace.input_tokens), _token_count(trace.output_tokens)
    line = f"Tokens: {tokens_in} in · {tokens_out} out"
    line += f" ({trace.reasoning_tokens:,} reasoning)" if trace.reasoning_tokens else ""
    return f"{line} · {trace.model}" if trace.model else line


def _token_count(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _render_final_step(step: TurnStep, settings: PanelSettings, key: str) -> None:
    if isinstance(step, NoteStep):
        _render_note(step)
    elif isinstance(step, ThinkingStep):
        _render_thinking(step, settings, key)
    elif isinstance(step, ToolStep):
        _render_tool(step, settings, key)


def _render_thinking(step: ThinkingStep, settings: PanelSettings, key: str) -> None:
    if not step.text:
        return
    expanded = settings.ui.reasoning.display == "expanded"
    teaser = thinking_teaser_markdown(step)
    with (
        st.expander(teaser, expanded=expanded, key=f"ayd-x-{key}"),
        st.container(key=f"ayd-thinking-{key}"),
    ):
        st.text(step.text)


def _render_tool(step: ToolStep, settings: PanelSettings, key: str) -> None:
    failed = step.status is StepStatus.FAILED
    with st.container(key=f"ayd-failed-{key}" if failed else f"ayd-step-{key}"):
        _render_tool_summary(step)
    for index, note in enumerate(step.notes):
        with st.container(key=f"ayd-warn-{key}-{index}"):
            st.text(f"ⓘ {note}")
    if settings.technical:
        _render_details(step, key)


def _render_details(step: ToolStep, key: str) -> None:
    with st.expander("Details", key=f"ayd-d-{key}"):
        st.text(f"tool  {step.name}")
        st.caption("model sent")
        st.code(step.model_args, language="json")
        if step.pinned_keys:
            pinned = plain_markdown(", ".join(step.pinned_keys))
            st.caption(f"actually sent — {pinned} added by your scope pin")
            st.code(step.sent_args, language="json")
        if step.meta_line:
            st.text(f"meta  {step.meta_line}")
        if step.preview:
            st.caption(f"result (first {len(step.preview):,} of {step.result_chars:,} chars)")
            st.code(step.preview, language=None)


def _render_live_steps(trace: TurnTrace) -> None:
    """The running body: plain lines only — no widgets, so repaints never clash on keys."""
    for step in trace.steps:
        if isinstance(step, NoteStep):
            _render_note(step)
        elif isinstance(step, ThinkingStep) and step.text:
            _render_live_thinking(step)
        elif isinstance(step, ToolStep):
            _render_tool_summary(step)


def _render_tool_summary(step: ToolStep) -> None:
    """The step's line and its first file chips — live and final panels alike."""
    st.markdown(tool_step_markdown(step))  # markdown, not st.text: only markdown shows icons
    if step.citations:
        st.caption(_chips(step.citations, limit=_CHIPS_PER_STEP))


def _render_live_thinking(step: ThinkingStep) -> None:
    if step.finished:
        st.markdown(thinking_teaser_markdown(step))
        return
    st.caption(iconed_markdown(THINKING_ICON, f"Thinking (live) · {len(step.text):,} chars"))
    st.text("\n".join(step.text.splitlines()[-_LIVE_THINKING_LINES:]))


def _note_line(step: NoteStep) -> str:
    return f"{_NOTE_PREFIX.get(step.kind, '')}{step.text}"


def _render_note(step: NoteStep) -> None:
    line, icon = _note_line(step), NOTE_ICONS.get(step.kind)
    if icon:  # the vision note; the page's own notes stay plain text
        st.markdown(iconed_markdown(icon, line))
    else:
        st.text(line)


def _chips(citations: Sequence[Citation], *, limit: int | None = None) -> str:
    """Code-span chips: markdown never applies inside a code span, so paths stay inert."""
    shown = citations if limit is None else citations[:limit]
    chips = " ".join(f"`{citation.label.replace('`', chr(39))}`" for citation in shown)
    more = len(citations) - len(shown)
    return f"{chips} +{more}" if more else chips


__all__ = (
    "LiveActivityPanel",
    "PanelItem",
    "PanelNote",
    "PanelSettings",
    "PanelSink",
    "render_saved_turn",
    "render_sources",
    "render_trace_body",
    "render_turn_footer",
    "status_state",
    "usage_line",
)
