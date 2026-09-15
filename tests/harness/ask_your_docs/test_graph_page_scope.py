"""AppTest smoke tests for the graph page's scope row (UI spec §6.11).

Every test seeds ``scope_capabilities``: the graph page never starts a server, so
the record it reads is whatever the chat page's last turn left in the session.
Runs where the [harness-ask-your-docs] extra is installed (the main checkout's
venv), skipped elsewhere.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("streamlit_agraph")

from pydocs_mcp.harness.ask_your_docs.attachments import AttachedSymbol
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities

from ._fixture import make_bundle

# page_env is autouse: importing it into this module is what arms it.
from ._page_fixtures import graph_page, page_env

_MAIN_SHA = "a" * 40
_FEATURE_SHA = "b" * 40
U1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)


@pytest.fixture
def workspace(tmp_path, page_env):
    """``demo`` with two branches, deliberately disagreeing on which one is "the" branch.

    The CHECKED-OUT (default) row is ``feature/retry`` and its base is ``main``, so the
    U0 caption and the attach (both the default row) name a DIFFERENT branch than the
    U1 selectbox (shipped ``branch_default: base``). A fixture where one branch is
    default AND base would pass against either rule.
    """
    make_bundle(
        tmp_path / "ws" / "demo_0123456789.db",
        members=[("mod_a", "Foo", "class"), ("mod_a", "bar", "function")],
        # A reference row is what puts ``mod_a`` in the module space the canvas draws;
        # without one the page stops at "Nothing to show here" before the Selected panel.
        refs=[("mod_a.Foo", "mod_a.bar", "calls")],
        branches=[
            ("main", _MAIN_SHA, None, 0, "active", None),
            ("feature/retry", _FEATURE_SHA, "main", 1, "active", None),
        ],
    )
    return tmp_path / "ws"


def test_sidebar_offers_the_where_to_search_popover_and_no_scope_button(workspace):
    """The popover is its own trigger (a Block in AppTest): its key exists, and no
    ``st.button`` labeled for scope is left in the sidebar."""
    at = graph_page()
    at.run()
    assert not at.exception, at.exception
    assert "graph_where_to_search" in at.session_state
    assert not any(b.label in {"Scope defaults", "Where to search"} for b in at.sidebar.button)


def test_the_popover_body_carries_the_chat_pages_picker_keys(workspace):
    """One component on both pages: the body's ``scope_picker_*`` keys, asserted on the
    children, never on a button."""
    at = graph_page()
    at.run()
    assert not at.exception, at.exception
    assert any(c.key == "scope_picker_project_demo" for c in at.checkbox)
    assert any(r.key == "scope_picker_code" for r in at.radio)
    assert not any(s.key == "scope_defaults_project" for s in at.selectbox)


def test_u0_branch_row_is_a_read_only_caption(workspace):
    """No advertised ``branch``: the row names the stamped branch, it never picks one."""
    at = graph_page()
    at.run()
    assert not at.exception, at.exception
    assert not any(s.key == "graph_branch" for s in at.selectbox)
    assert any(c.value == f"indexed on feature/retry @{_FEATURE_SHA[:7]}" for c in at.caption)


def test_u1_branch_selectbox_preselects_the_default_scope_branch(workspace):
    """``branch_default: base`` resolves to ``main`` — not the listing's first row."""
    at = graph_page(scope_capabilities=U1)
    at.run()
    assert not at.exception, at.exception
    box = at.selectbox(key="graph_branch")
    assert list(box.options) == ["feature/retry", "main"]
    assert box.value == "main"


def test_selected_panel_names_the_branch_the_symbol_was_read_from(workspace):
    at = graph_page(graph_selected="mod_a.Foo")
    at.run()
    assert not at.exception, at.exception
    assert any(c.value == "branch: feature/retry" for c in at.caption)


def test_add_to_question_attaches_the_project_and_the_branch(workspace):
    at = graph_page(graph_selected="mod_a.Foo")
    at.run()
    at.button(key="graph_attach").click().run()
    assert not at.exception, at.exception
    assert at.session_state["attached"] == [AttachedSymbol("mod_a.Foo", "demo", "feature/retry")]
