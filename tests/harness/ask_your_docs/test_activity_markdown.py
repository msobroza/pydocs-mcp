"""plain_markdown: model text in a step line renders as typed, never as an icon, style or link.

Each payload below is one Streamlit 1.59 markdown trigger that a backslash escape alone
does not stop (icon / logo / emoji shortcodes, entities, text directives, GFM autolinks).
"""

from __future__ import annotations

import pytest

from pydocs_mcp.harness.ask_your_docs.activity_markdown import (
    plain_markdown,
    thinking_teaser_markdown,
)
from pydocs_mcp.harness.ask_your_docs.activity_trace import ThinkingStep

_ZWSP = "\u200b"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("&colon;material&lowbar;bolt&colon;", r"\&colon;material\&lowbar;bolt\&colon;"),
        ("&colon;streamlit&colon;", r"\&colon;streamlit\&colon;"),
        ("a &amp; b", r"a \&amp; b"),
    ],
)
def test_an_html_entity_is_never_decoded(text: str, expected: str) -> None:
    """micromark decodes "&colon;" AFTER Streamlit's raw-string icon checks, so "&" is escaped."""
    assert plain_markdown(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://evil.example/x", f"https:{_ZWSP}//evil.example/x"),
        ("HTTP://evil.example", f"HTTP:{_ZWSP}//evil.example"),
        ("see www.evil.example", f"see www{_ZWSP}.evil.example"),
        ("WWW.evil.example", f"WWW{_ZWSP}.evil.example"),
        ("admin@evil.example", f"admin{_ZWSP}@evil.example"),
    ],
)
def test_a_bare_url_www_host_or_email_cannot_autolink(text: str, expected: str) -> None:
    assert plain_markdown(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (":red[x]", f":{_ZWSP}red\\[x\\]"),
        (":blue-background[x]", f":{_ZWSP}blue-background\\[x\\]"),
        (":violet-badge[x]", f":{_ZWSP}violet-badge\\[x\\]"),
        (":small[x]", f":{_ZWSP}small\\[x\\]"),
    ],
)
def test_a_text_directive_cannot_restyle_the_line(text: str, expected: str) -> None:
    assert plain_markdown(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (":material/thumb_up:", f":{_ZWSP}material/thumb\\_up:"),
        (":material_bolt:", f":{_ZWSP}material\\_bolt:"),
        (":white_check_mark:", f":{_ZWSP}white\\_check\\_mark:"),
    ],
)
def test_an_underscore_shortcode_is_defused_and_keeps_its_slash(text: str, expected: str) -> None:
    """Defused BEFORE escaping: an escaped "\\_" used to hide the name from the lookahead."""
    assert plain_markdown(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("std::string::npos", f"std::{_ZWSP}string::npos"),
        ("10:30:00", f"10:{_ZWSP}30:00"),
        ("key:value:other", f"key:{_ZWSP}value:other"),
        ("a: b", "a: b"),
    ],
)
def test_colon_text_that_looks_like_a_shortcode_is_defused_too(text: str, expected: str) -> None:
    """Pins the chosen trade-off: Streamlit's emoji name set lives only in its JS bundle."""
    assert plain_markdown(text) == expected


def test_a_reasoning_teaser_cannot_restyle_the_thinking_label() -> None:
    step = ThinkingStep(text="Try :small[x] at https://evil.example", started_at=None)
    label = thinking_teaser_markdown(step)
    assert f":{_ZWSP}small\\[x\\]" in label and f"https:{_ZWSP}//" in label
