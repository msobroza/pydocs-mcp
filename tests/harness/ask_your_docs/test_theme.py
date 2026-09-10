"""Regression: the chrome-hiding CSS must not hide the whole ``stToolbar``.

Streamlit renders ``stExpandSidebarButton`` — the only control that reopens a
collapsed sidebar — inside that container, and the collapsed state persists
across page reloads, so ``display: none`` on the container strands the user
with no way to bring the sidebar back. Only the narrower chrome pieces
(deploy/menu actions, status widget, decoration strip, footer) may be hidden.
"""

from __future__ import annotations

from pydocs_mcp.harness.ask_your_docs.theme import theme_css

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
    "footer",
)
# The main menu carries Streamlit's System / Light / Dark theme picker — the ONLY way to
# switch the theme since the in-app Light-mode toggle was removed (0.6.1 owner report).
_THEME_MENU = ("#MainMenu", '[data-testid="stMainMenu"]', '[data-testid="stMainMenuButton"]')


def test_toolbar_container_stays_visible() -> None:
    assert _TOOLBAR_CONTAINER not in theme_css(), (
        "hiding the stToolbar container also hides "
        "stExpandSidebarButton — a collapsed sidebar becomes unrecoverable"
    )


def test_narrow_chrome_pieces_still_hidden() -> None:
    css = theme_css()
    for selector in _HIDDEN_CHROME:
        assert selector in css, f"expected {selector} to stay hidden"


def test_the_theme_menu_stays_reachable() -> None:
    css = theme_css()
    for selector in _THEME_MENU:
        assert selector not in css, f"{selector} hides Streamlit's theme picker"
