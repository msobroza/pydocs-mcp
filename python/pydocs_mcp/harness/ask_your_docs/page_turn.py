"""One question on the chat page: its inputs, its run, its panel and what the page keeps.

Split out of ``app.py`` (which stays the composition root and the page's ONE error
boundary): collecting the attached images, refusing before any call, running the turn on
this page's serve session, and — with ``ask_your_docs.ui.activity.enabled`` — the
activity panel's per-turn bookkeeping. Every assistant turn's trace is kept in session
state under :data:`ACTIVITY_KEY` (message index -> TurnTrace), failed and stopped turns
included (stored as an empty assistant message plus their trace), and redrawn on rerun.

Example:
    panel = open_turn_panel(settings, redact, scope)
    outcome = answer_question(woven, handle, bearer, turn, TurnRunners(reformulate, ask), panel)
    finish_turn(panel, outcome.result, reasoning_caption)
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import functools
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.activity_labels import rephrase_note, scope_note
from pydocs_mcp.harness.ask_your_docs.activity_trace import (
    TraceLimits,
    TurnTrace,
    trim_history,
    turn_activity_record,
)
from pydocs_mcp.harness.ask_your_docs.activity_trace_builder import TraceBuilder
from pydocs_mcp.harness.ask_your_docs.activity_view import (
    LiveActivityPanel,
    PanelNote,
    PanelSettings,
    PanelSink,
    render_saved_turn,
    render_sources,
    render_turn_footer,
)
from pydocs_mcp.harness.ask_your_docs.attachments import ImageAttachment, validate_attachment
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerSource,
    redact_bearer,
    translate_auth_errors,
)

if TYPE_CHECKING:
    from pydocs_mcp.harness.ask_your_docs.page_agent import PageAgentHandle, PageTurnOutcome
    from pydocs_mcp.harness.ask_your_docs.reasoning_caption import ReasoningCaption
    from pydocs_mcp.retrieval.config.ask_your_docs_image_models import ImagesConfig
    from pydocs_mcp.retrieval.config.ask_your_docs_ui_models import AskYourDocsUiConfig

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")  # app.py's logger

ACTIVITY_KEY = "activity"  # message index -> TurnTrace
TECHNICAL_TOGGLE_KEY = "ayd_technical_details"
_SPINNER_TEXT = "searching your docs…"


@dataclass(frozen=True, slots=True)
class AskTurn:
    """What one question carries beyond its text — the per-turn inputs of ``ask``."""

    scope: dict[str, str]
    images: tuple[ImageAttachment, ...]
    prior_images: dict[str, ImageAttachment]  # PRIOR turns only — see app.py's snapshot note
    transient_note: str


@dataclass(frozen=True, slots=True)
class TurnRunners:
    """``reformulate`` + ``ask``, handed in by the page script each run.

    WHY not imported here: AppTest re-executes app.py, so ITS ``from … import`` picks up a
    test's swap of either — a name bound once in this module at import would not."""

    reformulate: Callable[..., Awaitable[str]]
    ask: Callable[..., Awaitable[str]]


# ── before the turn ──


def collect_images(files: list[Any], images_config: ImagesConfig) -> tuple[ImageAttachment, ...]:
    """UploadedFiles → validated ImageAttachments; violations render an
    inline error chip and drop the offending file (spec §3.6)."""
    if len(files) > images_config.max_per_turn:
        st.warning(
            f"only the first {images_config.max_per_turn} images were kept (images.max_per_turn)"
        )
    collected: list[ImageAttachment] = []
    for upload in files[: images_config.max_per_turn]:
        attachment = ImageAttachment(
            name=upload.name,
            media_type=upload.type or "application/octet-stream",
            data_b64=base64.b64encode(upload.getvalue()).decode(),
        )
        try:
            validate_attachment(attachment, images_config)
        except ValueError as exc:
            st.error(str(exc))
            continue
        collected.append(attachment)
    return tuple(collected)


def refuse(question: str, message: str, bearer: BearerSource) -> NoReturn:
    """Fail loudly BEFORE any LLM call: nothing is sent, the question stays visible. The one
    boundary between a failure and the browser (H4): every text crosses ``redact_bearer``."""
    st.error(redact_bearer(message, bearer))
    st.info(f"Your question (not sent): {question}")
    st.stop()


def turn_progress(ui: AskYourDocsUiConfig) -> contextlib.AbstractContextManager[Any]:
    """With the panel off, today's spinner around the whole answer; with it on, nothing."""
    return contextlib.nullcontext() if ui.activity.enabled else st.spinner(_SPINNER_TEXT)


def open_turn_panel(
    settings: PanelSettings, redact: Callable[[str], str], scope: dict[str, str]
) -> LiveActivityPanel | None:
    """The running turn's panel (drawn now, inside the assistant bubble), or None when off."""
    ui = settings.ui
    if not ui.activity.enabled:
        return None
    builder = TraceBuilder(limits=TraceLimits.from_ui_config(ui), redact=redact, scope=scope)
    index = len(st.session_state.messages)
    on_stopped = functools.partial(_save_unanswered, keep=ui.activity.history_keep)
    return LiveActivityPanel(builder, settings, f"t{index}", on_stopped)


# ── the turn ──


# WHY both calls sit inside translate_auth_errors: reformulate is text-only by contract
# (§3.6) — it runs on the woven question BEFORE image blocks are attached — and both drive a
# factory-built model, so a 401 is a raw SDK error whose body echoes the presented
# credential until this boundary turns it into a BearerRejectedError (E4, H4).
def answer_question(
    woven: str,
    handle: PageAgentHandle,
    bearer: BearerSource,
    turn: AskTurn,
    runners: TurnRunners,
    panel: LiveActivityPanel | None,
) -> PageTurnOutcome[str]:
    """Reformulate, then answer — one turn on this page's serve session, ONE auth boundary."""
    sink = None if panel is None else panel.sink
    live = panel is None or panel.settings.ui.activity.live
    body = _turn_body(woven, turn, runners, st.session_state.history, sink, live)
    with translate_auth_errors(bearer):
        future = asyncio.run_coroutine_threadsafe(handle.run_turn(body), handle.loop)
        return future.result() if panel is None else panel.drain(future)


def _turn_body(
    woven: str,
    turn: AskTurn,
    runners: TurnRunners,
    history: list[Any],
    sink: PanelSink | None,
    live: bool,
) -> Callable[[Any, Any], Awaitable[str]]:
    # No sink: exactly today's ask() call, so the eval-facing default stays byte-identical.
    activity: dict[str, Any] = {} if sink is None else {"on_event": sink, "live": live}

    async def answer_turn(agent: Any, llm: Any) -> str:
        standalone = await runners.reformulate(llm, history, woven)
        if sink is not None:
            sink(PanelNote(rephrase_note(woven, standalone), "rephrase"))
            sink(PanelNote(scope_note(turn.scope), "scope"))
        return await runners.ask(
            agent,
            history,
            standalone,
            scope=turn.scope,
            images=turn.images,
            image_store=turn.prior_images,
            transient_note=turn.transient_note,
            **activity,
        )

    return answer_turn


# ── after the turn ──


def finish_turn(panel: LiveActivityPanel | None, answer: str, caption: ReasoningCaption) -> None:
    """Draw the finished panel and the Sources row; keep the trace; teach the ladder."""
    if panel is None:
        return
    trace = panel.finish()
    index = len(st.session_state.messages)  # the assistant message the page appends next
    render_sources(trace.citations, answer, f"t{index}")
    _save_trace(index, trace, panel.settings.ui.activity.history_keep)
    caption.observe(trace.reasoning)  # the sidebar line updates in this same run


def fail_turn(
    panel: LiveActivityPanel, question: str, caption: str, error_class: str, *, released: bool
) -> NoReturn:
    """A turn that failed after it was sent: kept (A1) with its steps, the error shown."""
    trace = panel.fail(caption, error_class, released=released)
    render_turn_footer(trace, "", question, f"t{len(st.session_state.messages)}")
    _save_unanswered(trace, keep=panel.settings.ui.activity.history_keep)
    st.stop()


def _save_unanswered(trace: TurnTrace, *, keep: int) -> None:
    """A failed or stopped turn: an empty assistant message, so no user message is orphaned."""
    st.session_state.messages.append(("assistant", ""))
    _save_trace(len(st.session_state.messages) - 1, trace, keep)


def _save_trace(index: int, trace: TurnTrace, keep: int) -> None:
    traces = {**st.session_state.get(ACTIVITY_KEY, {}), index: trace}
    st.session_state[ACTIVITY_KEY] = trim_history(traces, keep)
    # INFO: diagnostics only — counts and states, never content (dropped by default under
    # `streamlit run`, which leaves the root logger unconfigured).
    log.info(json.dumps(turn_activity_record(trace)))


# ── every run ──


def render_history(settings: PanelSettings) -> None:
    """Every past message; an assistant turn with a kept trace redraws its panel first."""
    messages = st.session_state.messages
    traces = st.session_state.get(ACTIVITY_KEY, {})
    for index, (role, text) in enumerate(messages):
        with st.chat_message(role):
            trace = traces.get(index)
            if trace is None:
                st.markdown(text)
                continue
            question = messages[index - 1][1] if index else ""
            render_saved_turn(trace, text, question, settings, f"t{index}")


def technical_details_toggle(ui: AskYourDocsUiConfig) -> bool:
    """The session-only "Show technical details" toggle; its default comes from YAML."""
    if not ui.activity.enabled:
        return False
    default = ui.activity.technical_details
    return st.toggle("Show technical details", value=default, key=TECHNICAL_TOGGLE_KEY)


__all__ = (
    "ACTIVITY_KEY",
    "TECHNICAL_TOGGLE_KEY",
    "AskTurn",
    "TurnRunners",
    "answer_question",
    "collect_images",
    "fail_turn",
    "finish_turn",
    "open_turn_panel",
    "refuse",
    "render_history",
    "technical_details_toggle",
    "turn_progress",
)
