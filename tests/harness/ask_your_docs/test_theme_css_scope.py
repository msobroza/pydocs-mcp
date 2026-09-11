"""``theme_css`` is brand/accent/bubble styling only — text readability never depends on it.

Streamlit's native ``[theme.light]`` / ``[theme.dark]`` palettes own every text and ground
colour; if the injected CSS painted text or opaque grounds it could put dark text on a dark
ground again (the 0.6.1 owner report). It may only use the accent, its wash, the border and
the panel's danger/warn tokens (as ``light-dark()`` pairs, see
test_theme_css_follows_native_scheme); muted text is an opacity over native text.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.harness.ask_your_docs.theme import MUTED_TEXT_OPACITY, THEMES, theme_css

# Tokens the native theme owns: a hard-coded copy in the CSS would override it.
_NATIVE_TOKENS = ("bg", "surface", "recessed", "text", "muted")
# Native widgets and surfaces the old overlay re-themed by hand.
_NATIVE_SELECTORS = (
    '[data-testid="stWidgetLabel"]',
    '[data-testid="stCaptionContainer"]',
    '[data-baseweb="select"]',
    "stSelectboxVirtualDropdown",
    '[data-testid="stBottom"]',
    ".stChatInput",
    ".stButton button {",
    'section[data-testid="stSidebar"] {',
    "pre {",
    "code {",
)
_BRAND_TOUCHES = (
    ".brand .accent",
    ':has([aria-label="Chat message from user"])',
    '[data-testid="stChatMessageAvatarAssistant"]',
    '[data-testid="stSidebarNav"] a[aria-current="page"]',
)


@pytest.mark.parametrize("palette", sorted(THEMES))
@pytest.mark.parametrize("token", _NATIVE_TOKENS)
def test_css_never_paints_a_native_owned_colour(palette: str, token: str) -> None:
    assert THEMES[palette][token] not in theme_css()


def test_css_leaves_native_widgets_alone() -> None:
    css = theme_css()
    assert [s for s in _NATIVE_SELECTORS if s in css] == []


@pytest.mark.parametrize("palette", sorted(THEMES))
def test_css_keeps_the_brand_accent_and_bubble(palette: str) -> None:
    css = theme_css()
    assert THEMES[palette]["accent"] in css and THEMES[palette]["wash"] in css
    assert [s for s in _BRAND_TOUCHES if s not in css] == []


def _channel(value: float) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[float, ...]) -> float:
    r, g, b = rgb
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _rgb(hex_colour: str) -> tuple[float, ...]:
    return tuple(float(int(hex_colour[i : i + 2], 16)) for i in (1, 3, 5))


@pytest.mark.parametrize("palette", sorted(THEMES))
@pytest.mark.parametrize("ground", ["bg", "surface"])
def test_muted_opacity_keeps_native_text_readable(palette: str, ground: str) -> None:
    """Muted text is the native text colour at ``MUTED_TEXT_OPACITY``: still 4.5:1."""
    alpha = float(MUTED_TEXT_OPACITY)
    text, under = _rgb(THEMES[palette]["text"]), _rgb(THEMES[palette][ground])
    blended = tuple(alpha * t + (1 - alpha) * u for t, u in zip(text, under, strict=True))
    light, dark = sorted((_luminance(blended), _luminance(under)), reverse=True)
    assert (light + 0.05) / (dark + 0.05) >= 4.5
