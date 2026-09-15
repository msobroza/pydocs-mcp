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
from pydocs_mcp.harness.ask_your_docs.scope_picker import PICKER_TITLE
from pydocs_mcp.harness.ask_your_docs.strip_state import StripState, StripTarget

from ._fixture import make_bundle

# page_env is autouse: importing it into this module is what arms it.
from ._page_fixtures import graph_page, page_env

_MAIN_SHA = "a" * 40
_FEATURE_SHA = "b" * 40
_HOTFIX_SHA = "c" * 40
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


@pytest.fixture
def three_branch_workspace(tmp_path, page_env):
    """``demo`` with a THIRD branch, so a strip target can be non-degenerate.

    ``hotfix/timeouts`` is neither the default row (``feature/retry``) nor the first
    pickable row (rows come back ``is_default DESC, name``, so the order is
    ``feature/retry, hotfix/timeouts, main``) nor the base the YAML default resolves to
    (``main``). A target on it therefore cannot be matched by a row rule that read the
    listing, the compiled scope, or the selectbox's index-0 fallback.
    """
    make_bundle(
        tmp_path / "ws" / "demo_0123456789.db",
        members=[("mod_a", "Foo", "class"), ("mod_a", "bar", "function")],
        refs=[("mod_a.Foo", "mod_a.bar", "calls")],
        branches=[
            ("main", _MAIN_SHA, None, 0, "active", None),
            ("feature/retry", _FEATURE_SHA, "main", 1, "active", None),
            ("hotfix/timeouts", _HOTFIX_SHA, "main", 0, "active", None),
        ],
    )
    return tmp_path / "ws"


def test_sidebar_offers_exactly_one_where_to_search_button(workspace):
    """AC-45: one popover keyed ``graph_where_to_search``, labeled "Where to search", and
    none of the 2026-09-04 keys. A popover's label is NOT reachable from AppTest on 1.59
    (it is not a Button element), so the label is pinned through the constant the page
    passes."""
    at = graph_page()
    at.run()
    assert not at.exception, at.exception
    assert PICKER_TITLE == "Where to search"  # the only pin of the on-screen wording
    assert not any(b.label in {"Where to search", "Scope defaults"} for b in at.sidebar.button)
    assert "graph_where_to_search" in at.session_state  # the popover's key proves it rendered
    keys = {w.key for w in [*at.button, *at.checkbox, *at.selectbox, *at.radio] if w.key}
    assert not keys & {"scope_defaults_button", "scope_defaults_project", "scope_defaults_code"}


def test_the_button_opens_the_same_picker_as_the_chat_page(workspace):
    """One component on both pages: the body's ``scope_picker_*`` keys, asserted on the
    children, never on a button."""
    at = graph_page()
    at.run()
    assert not at.exception, at.exception
    assert any(c.key == "scope_picker_project_demo" for c in at.checkbox)
    assert any(r.key == "scope_picker_code" for r in at.radio)
    assert any(b.key == "scope_picker_use" for b in at.button)


def test_the_strips_branch_moves_the_row_off_the_base_branch(three_branch_workspace):
    """The picker feeds the row: a strip target on ``hotfix/timeouts`` preselects it, not
    the base (``main``) the YAML default resolves to and not the first row the selectbox
    falls back to. "Only these" is OFF on purpose — the strip then compiles to a DEFAULT
    whose cell is ``(demo, "")``, so a row that read the branch from the COMPILED scope
    would fall through to the base and fail here: the row must read the strip's target.
    Seeded before the first run — a selectbox holding a value ignores a changed ``index``.
    """
    at = graph_page(
        scope_capabilities=U1,
        scope_strip=StripState(targets=(StripTarget("demo", ("hotfix/timeouts",)),)),
    )
    at.run()
    assert not at.exception, at.exception
    assert list(at.selectbox(key="graph_branch").options) == [
        "feature/retry",
        "hotfix/timeouts",
        "main",
    ]
    assert at.selectbox(key="graph_branch").value == "hotfix/timeouts"


def test_a_target_this_workspace_never_indexed_is_dropped_before_the_row_renders(workspace):
    """E12 on this page too: it owns a Workspace box, so a strip edited against another
    workspace is narrowed here — not carried un-narrowed into the chat page."""
    at = graph_page(scope_strip=StripState(targets=(StripTarget("gone", ("main",)),)))
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["scope_strip"].targets == ()


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
