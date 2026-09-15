"""The two scope writes AppTest cannot observe, pinned in bare mode (UI spec §6.7, §6.9).

AppTest clicks widgets, never the page around them, so an outside-click dismissal has no
scripted equivalent at all; and ``store_strip_state``'s re-seed is MASKED on the chat page,
because its one state-changing caller ("Keep searching") reruns immediately and a mid-script
rerun already drops the widget keys the picker never got to render. Both writes are
contracts of their own functions, so both are asserted against ``st.session_state``
directly — a page test would pass with either one deleted.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.scope_picker import (
    ONLY_THESE_KEY,
    PICKER_KEY,
    STRIP_STATE_KEY,
    _forget_when_closed,
)
from pydocs_mcp.harness.ask_your_docs.scope_strip import store_strip_state
from pydocs_mcp.harness.ask_your_docs.strip_state import StripState, StripTarget

_HALF_EDITED = {
    "scope_picker_project_backend": True,
    "scope_picker_branches_backend": ["main"],
    "scope_picker_code": "own",
}
_HELD = StripState(targets=(StripTarget("tooling", ("main",)),), only_these=True)
_GROWN = _HELD.with_target(StripTarget("backend", ("feature/retry",)))


@pytest.fixture
def bare_session():
    """Bare-mode session state, emptied around the test (it is a process-wide singleton)."""
    st.session_state.clear()
    yield st.session_state
    st.session_state.clear()


def _seed(session, *, popover_open: bool = False) -> None:
    session.update(_HALF_EDITED)
    session[STRIP_STATE_KEY] = _HELD
    session[ONLY_THESE_KEY] = True
    session[PICKER_KEY] = popover_open


def _picker_keys(session) -> list[str]:
    return [k for k in session if str(k).startswith("scope_picker_")]


def test_a_closed_popover_forgets_every_half_edited_row(bare_session):
    _seed(bare_session)
    _forget_when_closed(PICKER_KEY)
    assert _picker_keys(bare_session) == []
    # Only the picker's OWN widgets go: the strip and its checkbox are not the picker's.
    assert bare_session[STRIP_STATE_KEY] == _HELD
    assert bare_session[ONLY_THESE_KEY] is True


def test_an_open_popover_keeps_the_rows(bare_session):
    _seed(bare_session, popover_open=True)
    _forget_when_closed(PICKER_KEY)
    assert {k: bare_session[k] for k in _HALF_EDITED} == _HALF_EDITED


def test_a_grown_strip_re_seeds_the_picker_and_the_only_these_box(bare_session):
    """A "Keep searching" chip's state goes through the full re-seed (AC-31), so the rows
    and the box follow it rather than the tick they held."""
    _seed(bare_session)
    store_strip_state(_GROWN)
    assert bare_session[STRIP_STATE_KEY] == _GROWN
    assert _picker_keys(bare_session) == []
    assert ONLY_THESE_KEY not in bare_session


def test_an_unchanged_strip_keeps_the_picker_ticks(bare_session):
    """The other chips hand the strip back untouched: sending their canned question must
    not discard ticks the person has not pressed "Use these" on yet."""
    _seed(bare_session)
    store_strip_state(_HELD)
    assert {k: bare_session[k] for k in _HALF_EDITED} == _HALF_EDITED
    assert bare_session[ONLY_THESE_KEY] is True
