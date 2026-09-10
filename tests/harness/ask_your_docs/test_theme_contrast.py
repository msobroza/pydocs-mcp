"""Every text-on-background pair in ``THEMES`` reaches WCAG AA (4.5:1), in both palettes.

The palettes feed Streamlit's native ``[theme.light]`` / ``[theme.dark]`` sections, so each
text token (body, muted, accent for links and inline code, the activity panel's danger and
warn) renders on each ground (canvas, raised surface, recessed inputs/code) — and the accent
also on its own translucent ``wash`` chip. Colour is never the only signal — every panel
state is also spelled out in words.
"""

from __future__ import annotations

import re

import pytest

from pydocs_mcp.harness.ask_your_docs.theme import THEMES, theme_css

_AA_TEXT_RATIO = 4.5
_TEXT_TOKENS = ("text", "muted", "accent", "danger", "warn")
_GROUNDS = ("bg", "surface", "recessed")


def _channel(value: float) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return r, g, b


def _luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = rgb
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    light, dark = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _over(rgba: str, ground: str) -> tuple[float, float, float]:
    """An ``rgba(r, g, b, a)`` wash composited over an opaque hex ground."""
    r, g, b, alpha = (float(x) for x in re.findall(r"[\d.]+", rgba))
    base = _rgb(ground)
    red, green, blue = (
        alpha * top + (1 - alpha) * under for top, under in zip((r, g, b), base, strict=True)
    )
    return red, green, blue


def test_both_palettes_define_the_same_tokens() -> None:
    assert set(THEMES) == {"light", "dark"}
    assert set(THEMES["light"]) == set(THEMES["dark"])


@pytest.mark.parametrize("palette", sorted(THEMES))
@pytest.mark.parametrize("token", _TEXT_TOKENS)
@pytest.mark.parametrize("ground", _GROUNDS)
def test_text_tokens_reach_aa_contrast(palette: str, token: str, ground: str) -> None:
    colours = THEMES[palette]
    ratio = _contrast(_rgb(colours[token]), _rgb(colours[ground]))
    assert ratio >= _AA_TEXT_RATIO, f"{palette}: {token} on {ground} is {ratio:.2f}:1"


@pytest.mark.parametrize("palette", sorted(THEMES))
@pytest.mark.parametrize("ground", _GROUNDS)
def test_accent_reads_on_its_wash_chip(palette: str, ground: str) -> None:
    colours = THEMES[palette]
    chip = _over(colours["wash"], colours[ground])
    ratio = _contrast(_rgb(colours["accent"]), chip)
    assert ratio >= _AA_TEXT_RATIO, f"{palette}: accent on wash over {ground} is {ratio:.2f}:1"


@pytest.mark.parametrize("palette", sorted(THEMES))
def test_the_panel_css_uses_the_tokens(palette: str) -> None:
    css = theme_css(THEMES[palette])
    assert THEMES[palette]["danger"] in css and THEMES[palette]["warn"] in css
    assert "st-key-ayd-thinking" in css and "st-key-ayd-failed" in css
