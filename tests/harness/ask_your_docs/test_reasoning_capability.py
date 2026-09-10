"""The reasoning availability ladder (activity panel, PROPOSAL §4, TDD 6).

Pure functions — no langchain, no Streamlit. Rung order: YAML > observed this session >
listing metadata (positive-only) > unknown. It never spends a probe call.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.harness.ask_your_docs.reasoning_capability import (
    ReasoningAvailability,
    ReasoningLadderState,
    TurnReasoning,
    classify_turn_reasoning,
    observe_turn,
    reasoning_availability,
    reasoning_sidebar_text,
    reasoning_turn_sentence,
)

_LISTED = {"id": "qwen/qwen3.8-27b", "supported_parameters": ["tools", "reasoning"]}


def _observed(*turns: TurnReasoning) -> ReasoningLadderState:
    state = ReasoningLadderState()
    for turn in turns:
        state = observe_turn(state, turn)
    return state


def _rung(state=None, *, configured=None, display_hidden=False, entry=None):
    return reasoning_availability(
        state or ReasoningLadderState(),
        configured=configured,
        display_hidden=display_hidden,
        listing_entry=entry,
    )


# ── one turn ──


@pytest.mark.parametrize(
    ("text_chars", "tokens", "redacted", "display_off", "expected"),
    [
        (12, 30, False, False, TurnReasoning.SHOWN),
        (0, 412, False, False, TurnReasoning.HIDDEN),
        (0, None, True, False, TurnReasoning.HIDDEN),
        (0, 0, False, False, TurnReasoning.NONE),
        (0, None, False, False, TurnReasoning.UNKNOWN),
        (12, 30, False, True, TurnReasoning.OFF),
    ],
)
def test_each_turn_lands_in_exactly_one_of_five_states(
    text_chars, tokens, redacted, display_off, expected
) -> None:
    turn = classify_turn_reasoning(
        text_chars=text_chars, reasoning_tokens=tokens, redacted=redacted, display_off=display_off
    )
    assert turn is expected


# ── the sidebar ladder ──


def test_yaml_rung_wins_over_everything() -> None:
    shown = _observed(TurnReasoning.SHOWN)
    assert _rung(shown, display_hidden=True) is ReasoningAvailability.OFF
    assert _rung(shown, configured=False) is ReasoningAvailability.OFF
    assert _rung(configured=True) is ReasoningAvailability.SUPPORTED_UNSEEN
    assert _rung(shown, configured=True) is ReasoningAvailability.SHOWN


def test_shown_sticks_once_seen() -> None:
    state = _observed(TurnReasoning.SHOWN, TurnReasoning.NONE, TurnReasoning.NONE)
    assert _rung(observe_turn(state, TurnReasoning.HIDDEN)) is ReasoningAvailability.SHOWN


def test_not_shared_needs_two_consecutive_zero_token_turns() -> None:
    assert _rung(_observed(TurnReasoning.NONE)) is ReasoningAvailability.UNKNOWN
    broken = _observed(TurnReasoning.NONE, TurnReasoning.UNKNOWN, TurnReasoning.NONE)
    assert _rung(broken) is ReasoningAvailability.UNKNOWN
    two = _observed(TurnReasoning.NONE, TurnReasoning.NONE)
    assert _rung(two) is ReasoningAvailability.NOT_SHARED


def test_hidden_survives_easy_turns_and_upgrades_to_shown() -> None:
    hidden = _observed(TurnReasoning.HIDDEN, TurnReasoning.NONE, TurnReasoning.NONE)
    assert _rung(hidden) is ReasoningAvailability.HIDDEN
    assert _rung(observe_turn(hidden, TurnReasoning.SHOWN)) is ReasoningAvailability.SHOWN


def test_listing_metadata_is_positive_only() -> None:
    assert _rung(entry=_LISTED) is ReasoningAvailability.SUPPORTED_UNSEEN
    include = {"supported_parameters": ["include_reasoning"]}
    assert _rung(entry=include) is ReasoningAvailability.SUPPORTED_UNSEEN
    assert _rung(entry={"supported_parameters": ["tools"]}) is ReasoningAvailability.UNKNOWN
    assert _rung(entry={"supported_parameters": "reasoning"}) is ReasoningAvailability.UNKNOWN
    assert _rung(entry=None) is ReasoningAvailability.UNKNOWN


def test_an_observation_beats_the_listing() -> None:
    not_shared = _observed(TurnReasoning.NONE, TurnReasoning.NONE)
    assert _rung(not_shared, entry=_LISTED) is ReasoningAvailability.NOT_SHARED


def test_an_off_display_turn_breaks_the_zero_token_streak() -> None:
    """ "Two consecutive turns with 0 reasoning tokens": an OFF turn is not one of them."""
    state = _observed(TurnReasoning.NONE, TurnReasoning.OFF, TurnReasoning.NONE)
    assert _rung(state) is ReasoningAvailability.UNKNOWN


# ── text ──


@pytest.mark.parametrize("availability", list(ReasoningAvailability))
def test_sidebar_text_never_parses_as_the_connection_status_line(availability) -> None:
    """The status-line fixture picks the first caption holding both " · " and "vision:"."""
    text = reasoning_sidebar_text(availability)
    assert text.startswith("Reasoning: ")
    assert " · " not in text and "vision:" not in text


def test_turn_sentences_name_the_case_in_words() -> None:
    hidden = reasoning_turn_sentence(
        TurnReasoning.HIDDEN, reasoning_tokens=412, host="openrouter.ai"
    )
    assert hidden == "The model reasoned (412 tokens), but openrouter.ai doesn't return the text."
    no_count = reasoning_turn_sentence(TurnReasoning.HIDDEN, reasoning_tokens=None, host="")
    assert no_count == "The model reasoned, but the provider doesn't return the text."
    none = reasoning_turn_sentence(TurnReasoning.NONE, reasoning_tokens=0, host="h")
    assert none == "This model didn't share any reasoning for this answer."
    unknown = reasoning_turn_sentence(TurnReasoning.UNKNOWN, reasoning_tokens=None, host="h")
    assert unknown == "This endpoint doesn't report whether the model reasoned."
    for quiet in (TurnReasoning.SHOWN, TurnReasoning.OFF):
        assert reasoning_turn_sentence(quiet, reasoning_tokens=3, host="h") == ""
