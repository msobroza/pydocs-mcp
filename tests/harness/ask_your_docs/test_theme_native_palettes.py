"""The launcher gives Streamlit BOTH native palettes and never pins a base theme.

Owner report (0.6.1): Light mode was unreadable because the native theme was pinned to
dark (``--theme.base dark``) and only a partial CSS overlay switched — every native element
it missed kept dark colours. Streamlit cannot switch its theme from Python, so the launcher
now emits ``[theme.light]`` and ``[theme.dark]`` from ``THEMES`` and the viewer switches with
Streamlit's own menu (its System / Light / Dark picker appears once both sections exist).

Core deps only: ``cli._require_extra`` is a no-op and ``subprocess.run`` is the named
``FakeStreamlitRun``, so nothing starts.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.harness.ask_your_docs import cli
from pydocs_mcp.harness.ask_your_docs.theme import THEMES, streamlit_theme_flags

from ._launcher_fakes import FakeStreamlitRun

# Streamlit option -> THEMES token, for the page and for the sidebar of each palette.
_EXPECTED_PAGE = {
    "primaryColor": "accent",
    "backgroundColor": "bg",
    "secondaryBackgroundColor": "surface",
    "textColor": "text",
    "linkColor": "accent",
    "codeTextColor": "accent",
    "codeBackgroundColor": "recessed",
    "borderColor": "border",
}
_EXPECTED_SIDEBAR = {"backgroundColor": "surface", "secondaryBackgroundColor": "recessed"}


def _theme_options(cmd: list[str]) -> dict[str, str]:
    """``{"theme.light.primaryColor": "#...", ...}`` for every ``--theme.*`` flag in argv."""
    return {
        token.removeprefix("--"): cmd[i + 1]
        for i, token in enumerate(cmd)
        if token.startswith("--theme.")
    }


@pytest.mark.parametrize("variant", ["light", "dark"])
def test_each_palette_is_emitted_natively(variant: str) -> None:
    options = _theme_options(streamlit_theme_flags())
    palette = THEMES[variant]
    for option, token in _EXPECTED_PAGE.items():
        assert options[f"theme.{variant}.{option}"] == palette[token], option
    for option, token in _EXPECTED_SIDEBAR.items():
        assert options[f"theme.{variant}.sidebar.{option}"] == palette[token], option


def test_no_base_theme_is_pinned() -> None:
    options = _theme_options(streamlit_theme_flags())
    assert "theme.base" not in options
    stray = [o for o in options if not o.startswith(("theme.light.", "theme.dark."))]
    assert stray == [], f"top-level [theme] options pin one look for both modes: {stray}"


def _extra_installed() -> None:
    """The launcher's extra guard, satisfied (no streamlit needed to record the spawn)."""


def test_the_launcher_passes_both_palettes(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeStreamlitRun()
    monkeypatch.setattr(cli, "_require_extra", _extra_installed)
    monkeypatch.setattr(cli.subprocess, "run", fake)
    assert cli.main([]) == 0
    options = _theme_options(fake.cmd)
    assert options["theme.light.backgroundColor"] == THEMES["light"]["bg"]
    assert options["theme.dark.backgroundColor"] == THEMES["dark"]["bg"]
    assert "theme.base" not in options
