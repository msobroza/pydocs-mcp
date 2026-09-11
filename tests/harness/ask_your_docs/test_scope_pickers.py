"""scope_pickers.render_scope_pickers — the sidebar's Project / Code / Package pins.

Moved out of ``app.py`` to keep the page inside its line budget; the widgets, their keys and
the pins they return are unchanged. Driven through ``AppTest.from_function`` with a fake
catalog, so no workspace is read.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest


def _picker_page() -> None:
    import streamlit as st

    from pydocs_mcp.harness.ask_your_docs.scope_pickers import render_scope_pickers

    def catalog(_workspace: str) -> dict[str, list[str]]:
        if st.session_state.get("broken"):
            raise OSError("unreadable workspace")
        return {"backend": ["fastapi", "pydantic"], "web": ["jinja2"], "tools": []}

    with st.sidebar:
        st.session_state["pins"] = tuple(
            render_scope_pickers(st.session_state.get("workspace", "/ws"), catalog)
        )


def _page(**seeds) -> AppTest:
    at = AppTest.from_function(_picker_page, default_timeout=60)
    for key, value in seeds.items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, at.exception
    return at


def test_defaults_pin_nothing() -> None:
    at = _page()
    assert at.session_state["pins"] == ("", "", "all")
    assert at.selectbox(key="scope_project").options == ["All projects", "backend", "web", "tools"]
    assert at.selectbox(key="scope_package").options == [
        "All packages",
        "fastapi",
        "jinja2",
        "pydantic",
    ]
    assert [c.value for c in at.caption] == ["Searches run only inside this scope."]


def test_a_project_pin_narrows_the_package_pool() -> None:
    at = _page()
    at.selectbox(key="scope_project").set_value("backend").run()
    assert at.selectbox(key="scope_package").options == ["All packages", "fastapi", "pydantic"]
    at.selectbox(key="scope_package").set_value("pydantic").run()
    assert at.session_state["pins"] == ("backend", "pydantic", "all")


def test_own_code_hides_the_package_picker() -> None:
    at = _page()
    at.radio(key="scope_code").set_value("Own code").run()
    assert at.session_state["pins"] == ("", "", "project")
    assert not [s for s in at.selectbox if s.key == "scope_package"]


def test_a_project_without_dependencies_has_no_package_picker() -> None:
    at = _page()
    at.selectbox(key="scope_project").set_value("tools").run()
    at.radio(key="scope_code").set_value("Dependencies").run()
    assert at.session_state["pins"] == ("tools", "", "deps")
    assert not [s for s in at.selectbox if s.key == "scope_package"]


def test_no_workspace_renders_no_pickers() -> None:
    at = _page(workspace="")
    assert at.session_state["pins"] == ("", "", "all") and not at.selectbox


def test_an_unreadable_workspace_warns_and_pins_nothing() -> None:
    at = _page(broken=True)
    assert at.session_state["pins"] == ("", "", "all") and not at.selectbox
    assert [w.value for w in at.warning] == ["Couldn't scan workspace: unreadable workspace"]
