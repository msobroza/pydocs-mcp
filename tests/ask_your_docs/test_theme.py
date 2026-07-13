"""Regression: the chrome-hiding CSS must not hide the whole ``stToolbar``.

Streamlit renders ``stExpandSidebarButton`` — the only control that reopens a
collapsed sidebar — inside that container, and the collapsed state persists
across page reloads, so ``display: none`` on the container strands the user
with no way to bring the sidebar back. Only the narrower chrome pieces
(deploy/menu actions, status widget, decoration strip, footer) may be hidden.
"""

from __future__ import annotations

from pydocs_mcp.ask_your_docs.theme import THEMES, theme_css

# Full selector strings: '[data-testid="stToolbar"]' cannot false-match inside
# '[data-testid="stToolbarActions"]' because of the closing quote-bracket.
_TOOLBAR_CONTAINER = '[data-testid="stToolbar"]'
_HIDDEN_CHROME = (
    '[data-testid="stToolbarActions"]',
    # The deploy button sits directly under stToolbar, NOT inside
    # stToolbarActions — it needs its own selector.
    '[data-testid="stAppDeployButton"]',
    '[data-testid="stStatusWidget"]',
    '[data-testid="stDecoration"]',
    "#MainMenu",
    "footer",
)


def test_toolbar_container_stays_visible() -> None:
    for name, palette in THEMES.items():
        css = theme_css(palette)
        assert _TOOLBAR_CONTAINER not in css, (
            f"{name}: hiding the stToolbar container also hides "
            "stExpandSidebarButton — a collapsed sidebar becomes unrecoverable"
        )


def test_narrow_chrome_pieces_still_hidden() -> None:
    for name, palette in THEMES.items():
        css = theme_css(palette)
        for selector in _HIDDEN_CHROME:
            assert selector in css, f"{name}: expected {selector} to stay hidden"
