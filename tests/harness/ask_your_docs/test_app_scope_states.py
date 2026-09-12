"""AppTest smoke tests for the three screen states — AC-19, 20, 21, 21b, 33.

Every test seeds ``scope_capabilities`` so the page never builds the agent
(no serve subprocess, no LLM client); ``page()`` adds the fake serve opener
and the connection seams every page test shares. Runs where the
[harness-ask-your-docs] extra is installed (the main checkout's venv),
skipped elsewhere.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    ScopeCell,
    ScopeCode,
    ScopeKind,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)

from ._fixture import make_bundle

# page_env is autouse: importing it into this module is what arms it.
from ._page_fixtures import page, page_env

_HEAD = "a" * 40
U1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)


@pytest.fixture
def workspace(tmp_path, page_env):
    """One bundle (project ``demo``, branches main + feature/retry) in page_env's workspace."""
    make_bundle(
        tmp_path / "ws" / "demo_0123456789.db",
        members=[("mod_a", "Foo", "class")],
        branches=[
            ("main", _HEAD, None, 1, "active", None),
            ("feature/retry", _HEAD, "main", 0, "active", None),
        ],
    )
    return tmp_path / "ws"


def _app(capabilities=NO_SCOPE_CAPABILITIES, **seeds):
    return page(scope_capabilities=capabilities, **seeds)


def _pin(*branches: str) -> QuestionScope:
    return QuestionScope(kind=ScopeKind.PIN, cells=tuple(ScopeCell("demo", b) for b in branches))


def test_state_1_default_view(workspace):
    """AC-19."""
    at = _app()
    at.run()
    assert not at.exception, at.exception
    assert any(b.label == "Scope defaults" for b in at.sidebar.button)
    widget_keys = {w.key for w in [*at.selectbox, *at.radio, *at.multiselect]}
    assert not widget_keys & {
        "scope_project",
        "scope_code",
        "scope_package",
        "scope_defaults_project",
    }
    assert not any("Searches run only inside this scope" in c.value for c in at.caption)
    assert any(b.key == "scope_pin_apply" for b in at.button)  # the popover rendered its children
    assert not any(m.key == "scope_pin_branches" for m in at.multiselect)


def test_state_2_panel_on_u0(workspace):
    """AC-20: controls + soft-defaults caption; the branch row is a read-only caption."""
    at = _app(scope_defaults_open=True)
    at.run()
    assert not at.exception, at.exception
    assert any(s.key == "scope_defaults_project" for s in at.selectbox)
    assert any(r.key == "scope_defaults_code" for r in at.radio)
    assert not any(r.key == "scope_defaults_slice" for r in at.radio)
    assert not any(s.key == "scope_defaults_branch" for s in at.selectbox)
    captions = [c.value for c in at.caption]
    assert any("Soft defaults" in c for c in captions)
    # The per-project form is the panel's own; the popover's caption lacks the name.
    assert any("demo — branch: main @aaaaaaa (checked out)" in c for c in captions)


def test_state_2_branch_controls_when_advertised(workspace):
    at = _app(U1, scope_defaults_open=True)
    at.run()
    assert not at.exception, at.exception
    assert any(s.key == "scope_defaults_branch" for s in at.selectbox)
    assert any(m.key == "scope_pin_branches" for m in at.multiselect)
    assert not any(r.key == "scope_pin_slice" for r in at.radio)


def test_state_3_pin_active(workspace):
    """AC-21: chips per cell + clear all; the transcript question carries its caption."""
    at = _app(
        scope_pin=_pin("main", "feature/retry"),
        messages=[
            {"role": "user", "text": "what is Foo?", "scope_caption": "demo · main, feature/retry"}
        ],
        history=[],
    )
    at.run()
    assert not at.exception, at.exception
    chip_keys = {b.key for b in at.button if b.key.startswith("scope_chip_")}
    assert chip_keys == {"scope_chip_demo_main", "scope_chip_demo_feature/retry"}
    assert any(b.key == "chip_clear" for b in at.button)
    assert any(c.value == "demo · main, feature/retry" for c in at.caption)


def test_clear_all_clears_the_kept_pin(workspace):
    """AC-21b (second half); the one-shot half is snapshot_pin_for_send's unit test."""
    at = _app(scope_pin=_pin("main"))
    at.run()
    at.button(key="chip_clear").click().run()
    assert not at.exception, at.exception
    assert at.session_state["scope_pin"] is None


def test_removing_the_last_cell_chip_clears_the_pin(workspace):
    at = _app(scope_pin=_pin("main"))
    at.run()
    at.button(key="scope_chip_demo_main").click().run()
    assert not at.exception, at.exception
    assert at.session_state["scope_pin"] is None


def test_removing_one_of_two_cell_chips_keeps_the_other(workspace):
    """The chip removes ITS cell — a mutant clearing the whole pin passes the test above."""
    at = _app(scope_pin=_pin("main", "feature/retry"))
    at.run()
    at.button(key="scope_chip_demo_main").click().run()
    assert not at.exception, at.exception
    assert at.session_state["scope_pin"] == _pin("feature/retry")


def test_reset_to_shipped_restores_the_yaml_values(workspace):
    """AC-33."""
    at = _app(scope_defaults_open=True)
    at.run()
    at.radio(key="scope_defaults_code").set_value(ScopeCode.OWN).run()
    assert at.radio(key="scope_defaults_code").value is ScopeCode.OWN
    at.button(key="scope_defaults_reset").click().run()
    assert not at.exception, at.exception
    assert at.radio(key="scope_defaults_code").value is ScopeCode.ALL
