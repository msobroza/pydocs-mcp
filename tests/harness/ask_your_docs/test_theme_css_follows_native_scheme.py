"""``theme_css`` picks each accent with CSS ``light-dark()``, never from a Python-side guess.

Visual check (Streamlit 1.59.1): switching Dark in Streamlit's main menu does NOT rerun the
script, so a palette chosen from ``st.context.theme.type`` stayed on the LIGHT accent
(#096B5A, ~2.9:1 on the dark canvas) for the brand and the active nav until the next rerun.
Streamlit sets ``color-scheme: light | dark`` on ``.stApp`` and the sidebar per native theme,
and ``light-dark(<light>, <dark>)`` resolves against it — so the switch is instant.
"""

from __future__ import annotations

import inspect

import pytest

from pydocs_mcp.harness.ask_your_docs.theme import THEMES, theme_css

# Every token the injected CSS paints; each must follow the native scheme.
_PAIRED_TOKENS = ("accent", "wash", "border", "danger", "warn")


def _pair(token: str) -> str:
    return f"light-dark({THEMES['light'][token]}, {THEMES['dark'][token]})"


def test_theme_css_takes_no_palette() -> None:
    assert list(inspect.signature(theme_css).parameters) == []


@pytest.mark.parametrize("token", _PAIRED_TOKENS)
def test_each_painted_token_is_a_light_dark_pair(token: str) -> None:
    assert _pair(token) in theme_css()


@pytest.mark.parametrize("token", _PAIRED_TOKENS)
def test_no_single_palette_colour_leaks_outside_a_pair(token: str) -> None:
    css = theme_css()
    for paired in _PAIRED_TOKENS:
        css = css.replace(_pair(paired), "")
    leaked = [v for v in (THEMES["light"][token], THEMES["dark"][token]) if v in css]
    assert leaked == [], f"{token}: {leaked} is painted outside light-dark()"
