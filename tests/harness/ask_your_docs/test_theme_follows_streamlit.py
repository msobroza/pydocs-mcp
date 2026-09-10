"""The page palette follows Streamlit's own theme; there is no in-app Light-mode toggle.

Owner decision (0.6.1 report): the viewer switches with Streamlit's main menu (System /
Light / Dark), so ``current_palette`` reads ``st.context.theme.type`` and neither page
renders a toggle or keeps the old ``ui_light`` session keys. AppTest reports no theme type
(``None``), which must resolve to the dark palette — as it does on a first load before the
browser has reported its theme.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.harness.ask_your_docs import theme
from pydocs_mcp.harness.ask_your_docs.theme import THEMES, palette_for_theme_type

# page_env is an autouse fixture: importing it arms it for this module.
from ._page_fixtures import page, page_env

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

_GRAPH_PAGE = theme.__file__.replace("theme.py", "pages/2_Graph.py")
_OLD_KEYS = {"ui_light", "ui_light_widget"}


@pytest.mark.parametrize(
    ("theme_type", "expected"),
    [("light", "light"), ("dark", "dark"), (None, "dark"), ("sepia", "dark")],
)
def test_palette_follows_the_streamlit_theme_type(theme_type: str | None, expected: str) -> None:
    assert palette_for_theme_type(theme_type) is THEMES[expected]


def test_the_in_app_toggle_is_gone() -> None:
    assert not hasattr(theme, "render_appearance_toggle")


def _assert_no_toggle(at: AppTest) -> None:
    assert not at.exception, at.exception
    assert "Light mode" not in [t.label for t in at.toggle]
    assert not _OLD_KEYS & set(at.session_state.filtered_state)


def test_the_chat_page_has_no_light_mode_toggle() -> None:
    at = page()
    at.run()
    _assert_no_toggle(at)


def test_the_graph_page_has_no_light_mode_toggle() -> None:
    at = AppTest.from_file(_GRAPH_PAGE, default_timeout=60)
    at.run()
    _assert_no_toggle(at)
