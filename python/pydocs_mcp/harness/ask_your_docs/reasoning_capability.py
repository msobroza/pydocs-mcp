"""Is the model's reasoning visible? The per-turn state and the sidebar ladder (PROPOSAL §4).

Two questions kept apart on purpose:

- **This turn** (:func:`classify_turn_reasoning`): shown, hidden by the provider, none, or
  unknown — decided from what the stream actually carried; ``off`` when YAML hides it.
- **This model** (:func:`reasoning_availability`): a ladder mirroring the vision one in
  ``multimodal.py`` — YAML > observed this session > listing metadata (positive-only) >
  unknown. It never spends a probe call; the page keeps one :class:`ReasoningLadderState`
  per connection key in session state and folds each finished turn in with
  :func:`observe_turn`.

Pure: no langchain, no Streamlit.

Example:
    >>> state = observe_turn(ReasoningLadderState(), TurnReasoning.SHOWN)
    >>> reasoning_availability(state, configured=None, display_hidden=False, listing_entry=None)
    <ReasoningAvailability.SHOWN: 'shown'>
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# WHY two: models skip reasoning on easy prompts, so one zero-token turn proves nothing.
_NOT_SHARED_AFTER_TURNS = 2
# /v1/models ``supported_parameters`` entries that advertise reasoning (OpenRouter listing).
_LISTING_REASONING_PARAMS = frozenset({"reasoning", "include_reasoning"})


class TurnReasoning(StrEnum):
    """What one finished turn showed about reasoning."""

    SHOWN = "shown"  # reasoning text arrived
    HIDDEN = "hidden"  # reasoning tokens > 0 or an encrypted detail, but no text
    NONE = "none"  # usage reported 0 reasoning tokens and no text came
    UNKNOWN = "unknown"  # no text and no usage: the endpoint doesn't say
    OFF = "off"  # ask_your_docs.ui.reasoning hides it


class ReasoningAvailability(StrEnum):
    """The sidebar's per-model answer; ``SHOWN`` sticks once seen."""

    SHOWN = "shown"
    HIDDEN = "hidden"
    NOT_SHARED = "not_shared"
    SUPPORTED_UNSEEN = "supported_unseen"
    UNKNOWN = "unknown"
    OFF = "off"


@dataclass(frozen=True, slots=True)
class ReasoningLadderState:
    """What this session has observed for one connection; replaced, never mutated."""

    observed: ReasoningAvailability | None = None
    zero_token_streak: int = 0


# A stronger observation replaces a weaker one, never the reverse: seeing text proves the
# model shares it, and seeing reasoning tokens proves it reasons even if later turns don't.
_OBSERVATION_RANK = {
    None: 0,
    ReasoningAvailability.NOT_SHARED: 1,
    ReasoningAvailability.HIDDEN: 2,
    ReasoningAvailability.SHOWN: 3,
}
_TURN_OBSERVATION = {
    TurnReasoning.SHOWN: ReasoningAvailability.SHOWN,
    TurnReasoning.HIDDEN: ReasoningAvailability.HIDDEN,
}
_SIDEBAR_TEXT = {
    ReasoningAvailability.SHOWN: "Reasoning: shown (seen in answers)",
    ReasoningAvailability.HIDDEN: "Reasoning: hidden by provider",
    ReasoningAvailability.NOT_SHARED: "Reasoning: not shared by this model",
    ReasoningAvailability.SUPPORTED_UNSEEN: "Reasoning: supported, not seen yet",
    ReasoningAvailability.UNKNOWN: "Reasoning: unknown until the first answer",
    ReasoningAvailability.OFF: "Reasoning: display off (config)",
}
_TURN_SENTENCES = {
    TurnReasoning.NONE: "This model didn't share any reasoning for this answer.",
    TurnReasoning.UNKNOWN: "This endpoint doesn't report whether the model reasoned.",
}


def classify_turn_reasoning(
    *, text_chars: int, reasoning_tokens: int | None, redacted: bool, display_off: bool
) -> TurnReasoning:
    """One turn's state; ``reasoning_tokens`` is ``None`` when no usage reported the count."""
    if display_off:
        return TurnReasoning.OFF
    if text_chars > 0:
        return TurnReasoning.SHOWN
    if redacted or (reasoning_tokens or 0) > 0:
        return TurnReasoning.HIDDEN
    return TurnReasoning.NONE if reasoning_tokens == 0 else TurnReasoning.UNKNOWN


def observe_turn(state: ReasoningLadderState, turn: TurnReasoning) -> ReasoningLadderState:
    """``state`` with one finished turn folded in; any non-``NONE`` turn breaks the streak."""
    streak = state.zero_token_streak + 1 if turn is TurnReasoning.NONE else 0
    seen = _TURN_OBSERVATION.get(turn)
    if seen is None and streak >= _NOT_SHARED_AFTER_TURNS:
        seen = ReasoningAvailability.NOT_SHARED
    stronger = seen if _OBSERVATION_RANK[seen] > _OBSERVATION_RANK[state.observed] else None
    return ReasoningLadderState(observed=stronger or state.observed, zero_token_streak=streak)


def reasoning_availability(
    state: ReasoningLadderState,
    *,
    configured: bool | None,
    display_hidden: bool,
    listing_entry: Mapping[str, Any] | None,
) -> ReasoningAvailability:
    """The ladder: YAML (``availability`` / ``display``) > observed > listing > unknown."""
    if display_hidden or configured is False:
        return ReasoningAvailability.OFF
    if state.observed is not None:
        return state.observed
    if configured is True or _listing_hints_reasoning(listing_entry):
        return ReasoningAvailability.SUPPORTED_UNSEEN
    return ReasoningAvailability.UNKNOWN


def _listing_hints_reasoning(entry: Mapping[str, Any] | None) -> bool:
    """Positive-only: most OpenAI-compatible servers publish no ``supported_parameters``."""
    params = entry.get("supported_parameters") if entry else None
    return isinstance(params, list) and any(p in _LISTING_REASONING_PARAMS for p in params)


def reasoning_sidebar_text(availability: ReasoningAvailability) -> str:
    """The sidebar caption. Never holds " · " or "vision:" — that shape is the status line."""
    return _SIDEBAR_TEXT[availability]


def reasoning_turn_sentence(turn: TurnReasoning, *, reasoning_tokens: int | None, host: str) -> str:
    """The muted sentence that ends a turn's steps, or "" when the steps speak for themselves."""
    if turn is TurnReasoning.HIDDEN:
        count = f" ({reasoning_tokens:,} tokens)" if reasoning_tokens else ""
        return f"The model reasoned{count}, but {host or 'the provider'} doesn't return the text."
    return _TURN_SENTENCES.get(turn, "")


__all__ = (
    "ReasoningAvailability",
    "ReasoningLadderState",
    "TurnReasoning",
    "classify_turn_reasoning",
    "observe_turn",
    "reasoning_availability",
    "reasoning_sidebar_text",
    "reasoning_turn_sentence",
)
