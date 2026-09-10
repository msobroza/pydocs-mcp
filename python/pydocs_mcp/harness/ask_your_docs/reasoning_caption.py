"""The sidebar's reasoning caption: drawn before the turn, redrawn once the turn taught it.

WHY a slot: the sidebar is drawn before the turn runs, so a caption written there once
would show the ladder as it stood BEFORE this answer until the next rerun. The caption
lives in an ``st.empty()`` made at sidebar time instead, and the finished turn refills it
in the same run. One ladder per connection key lives in session state (PROPOSAL §4).

Example:
    caption = render_reasoning_caption(ui, connection_key(connection))  # in the sidebar
    caption.observe(trace.reasoning)  # after the answer: teach the ladder, redraw
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.reasoning_capability import (
    ReasoningLadderState,
    TurnReasoning,
    observe_turn,
    reasoning_availability,
    reasoning_sidebar_text,
)

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config.ask_your_docs_ui_models import AskYourDocsUiConfig

_LADDERS_KEY = "reasoning_ladders"  # connection key -> ReasoningLadderState


@dataclass(frozen=True, slots=True)
class ReasoningCaption:
    """The sidebar slot and what fills it; ``slot`` is None while the panel is off."""

    ui: AskYourDocsUiConfig
    ladder_key: Hashable
    slot: Any  # the st.empty() placeholder

    def observe(self, reasoning: TurnReasoning) -> None:
        """Teach this connection's ladder one finished turn, then redraw the caption."""
        ladders = st.session_state.setdefault(_LADDERS_KEY, {})
        ladders[self.ladder_key] = observe_turn(self._ladder(), reasoning)
        self.redraw()

    def redraw(self) -> None:
        """Fill the slot from the ladder as it stands now."""
        if self.slot is None:
            return
        reasoning = self.ui.reasoning
        availability = reasoning_availability(
            self._ladder(),
            configured=reasoning.availability,
            display_hidden=reasoning.display == "hidden" or not reasoning.capture,
            # Known gap: the page's model listing keeps ids only, so the listing's
            # supported_parameters rung ("supported, not seen yet") is not consulted yet.
            listing_entry=None,
        )
        self.slot.caption(reasoning_sidebar_text(availability))

    def _ladder(self) -> ReasoningLadderState:
        ladders = st.session_state.get(_LADDERS_KEY, {})
        return ladders.get(self.ladder_key, ReasoningLadderState())


def render_reasoning_caption(ui: AskYourDocsUiConfig, ladder_key: Hashable) -> ReasoningCaption:
    """The sidebar's reasoning line — its OWN caption, never a cell of the status line."""
    caption = ReasoningCaption(ui, ladder_key, st.empty() if ui.activity.enabled else None)
    caption.redraw()
    return caption


__all__ = ("ReasoningCaption", "render_reasoning_caption")
