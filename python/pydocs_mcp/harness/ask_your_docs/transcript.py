"""The chat transcript: its entry shapes and its rendering (UI spec §6.7–§6.9).

Entries are dicts in ``st.session_state.messages`` — a user entry carries the
scope chip's caption, an assistant entry its answer footer and follow-up chips —
so a rerun re-renders the footer and the chips without re-asking (§6.8). An
assistant turn with a kept activity trace redraws its panel first.

Example:
    st.session_state.messages.append(user_transcript_entry("what is Foo?", "demo · main"))
    clicked = render_transcript(panel_settings)  # a follow-up chip the viewer clicked, or None
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.activity_view import PanelSettings, render_saved_turn
from pydocs_mcp.harness.ask_your_docs.answer_footer import FollowUpChip
from pydocs_mcp.harness.ask_your_docs.scope_panel import render_follow_up_chips

ACTIVITY_KEY = "activity"  # message index -> TurnTrace (page_turn writes it)
USER_ROLE = "user"
ASSISTANT_ROLE = "assistant"


def user_transcript_entry(text: str, scope_caption: str = "") -> dict[str, Any]:
    """A question as the transcript keeps it; ``scope_caption`` is "" under the defaults."""
    return {"role": USER_ROLE, "text": text, "scope_caption": scope_caption}


def assistant_transcript_entry(
    text: str, footer: str = "", chips: Sequence[FollowUpChip] = ()
) -> dict[str, Any]:
    """An answer with its footer line and chips; a failed turn keeps an empty one."""
    return {"role": ASSISTANT_ROLE, "text": text, "footer": footer, "chips": tuple(chips)}


def _question_before(messages: list[dict[str, Any]], index: int) -> str:
    return messages[index - 1]["text"] if index else ""


def _render_entry(
    index: int, entry: dict[str, Any], settings: PanelSettings, trace: Any
) -> FollowUpChip | None:
    if entry.get("scope_caption"):
        st.caption(entry["scope_caption"])
    if trace is None:
        st.markdown(entry["text"])
    else:
        question = _question_before(st.session_state.messages, index)
        render_saved_turn(trace, entry["text"], question, settings, f"t{index}")
    if entry["role"] != ASSISTANT_ROLE:
        return None
    if entry.get("footer"):
        st.caption(entry["footer"])
    return render_follow_up_chips(index, entry.get("chips", ()))


def render_transcript(settings: PanelSettings) -> FollowUpChip | None:
    """Every past message; returns the follow-up chip clicked this run, if any."""
    traces = st.session_state.get(ACTIVITY_KEY, {})
    clicked: FollowUpChip | None = None
    for index, entry in enumerate(st.session_state.messages):
        with st.chat_message(entry["role"]):
            clicked = _render_entry(index, entry, settings, traces.get(index)) or clicked
    return clicked


__all__ = (
    "ACTIVITY_KEY",
    "ASSISTANT_ROLE",
    "USER_ROLE",
    "assistant_transcript_entry",
    "render_transcript",
    "user_transcript_entry",
)
