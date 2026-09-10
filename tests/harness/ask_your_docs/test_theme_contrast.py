"""The activity panel's ``danger`` / ``warn`` tokens stay readable (PROPOSAL §6, TDD 10).

Both are text colours (a failed step's outcome, a warning note), so each must reach the
WCAG AA text ratio of 4.5:1 against the canvas and the raised surface, in both palettes.
Colour is never the only signal — every state is also spelled out in words.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.harness.ask_your_docs.theme import THEMES, theme_css

_AA_TEXT_RATIO = 4.5


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hex_colour: str) -> float:
    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast(a: str, b: str) -> float:
    light, dark = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@pytest.mark.parametrize("palette", sorted(THEMES))
@pytest.mark.parametrize("token", ["danger", "warn"])
@pytest.mark.parametrize("ground", ["bg", "surface"])
def test_panel_tokens_reach_aa_contrast(palette: str, token: str, ground: str) -> None:
    colours = THEMES[palette]
    assert _contrast(colours[token], colours[ground]) >= _AA_TEXT_RATIO


@pytest.mark.parametrize("palette", sorted(THEMES))
def test_the_panel_css_uses_the_tokens(palette: str) -> None:
    css = theme_css(THEMES[palette])
    assert THEMES[palette]["danger"] in css and THEMES[palette]["warn"] in css
    assert "st-key-ayd-thinking" in css and "st-key-ayd-failed" in css
