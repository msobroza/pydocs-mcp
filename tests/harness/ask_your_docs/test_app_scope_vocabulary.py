"""AC-47, rendered-tree half: neither page shows a retired segment in its default view,
and neither does the activity panel's scope line for a PIN question.

Runs where the [harness-ask-your-docs] extra is installed (the main checkout's venv,
PYTHONPATH at the worktree), skipped elsewhere. Both pages render over ONE indexed
project: an empty workspace draws fewer strings, and the strip narrows a seeded target
away against a listing that knows no project (E12), so the PIN below would never compile.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("streamlit_agraph")  # the graph page imports it at module level
pytest.importorskip("langgraph")  # the PIN run below drives the scripted ReAct graph

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
import pydocs_mcp.harness.ask_your_docs.reformulation as reformulation_module
from pydocs_mcp.harness.ask_your_docs.page_turn import TECHNICAL_TOGGLE_KEY
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import NO_SCOPE_CAPABILITIES
from pydocs_mcp.harness.ask_your_docs.strip_state import StripState, StripTarget

from ._agent_fakes import FakeActivityGraphBuilder
from ._connection_fakes import FakeBearer
from ._fixture import make_bundle

# page_env is autouse: the MODULE-LEVEL import is what arms it (pytest reads autouse
# fixtures from the module's globals at collection; a function-local import is too late).
from ._page_fixtures import graph_page, page, page_env, write_config
from .test_app_activity import _every_text, _same_question
from .test_scope_vocabulary import _RETIRED, _RETIRED_BUTTON_LABEL

_PROJECT = "backend"
_BRANCH = "feature/retry"


@pytest.fixture
def workspace(tmp_path, page_env):
    """One project whose stamped row is NOT its base, so the captions carry both names."""
    make_bundle(
        tmp_path / "ws" / f"{_PROJECT}_0123456789.db",
        project=_PROJECT,
        members=[("mod_a", "Foo", "class"), ("mod_a", "bar", "function")],
        refs=[("mod_a.Foo", "mod_a.bar", "calls")],
        packages=["fastapi"],  # a non-empty package pool, so the Package selectbox renders
        branches=[
            ("main", "a" * 40, None, 0, "active", None),
            (_BRANCH, "b" * 40, "main", 1, "active", None),
        ],
    )
    return tmp_path / "ws"


def _shown_strings(at) -> list[str]:
    """Every STRING the rendered tree carries — values, labels and the closed options.

    Only string-valued elements: a Button's ``.value`` is its clicked bool and would
    raise ``TypeError: argument of type 'bool' is not iterable`` in the containment
    check below.
    """
    shown = [e.value for e in [*at.markdown, *at.caption, *at.text, *at.error, *at.info]]
    shown += [w.label for w in [*at.button, *at.checkbox, *at.toggle]]
    shown += [w.label for w in [*at.radio, *at.selectbox, *at.multiselect]]
    shown += [w.label for w in [*at.status, *at.expander]]
    shown += [str(o) for w in [*at.radio, *at.selectbox, *at.multiselect] for o in w.options]
    return [s for s in shown if isinstance(s, str)]


def _retired_in(shown: list[str]) -> list[str]:
    return [s for s in shown if any(segment in s for segment in _RETIRED)]


@pytest.mark.parametrize("build", [page, graph_page], ids=["chat", "graph"])
def test_rendered_pages_show_none_of_the_retired_segments(workspace, build) -> None:
    at = build(scope_capabilities=NO_SCOPE_CAPABILITIES)
    at.run()
    assert not at.exception, at.exception
    shown = _shown_strings(at)
    assert shown, "the page rendered nothing — the sweep would pass on an empty tree"
    assert not _retired_in(shown)
    assert not [s for s in shown if s in {_RETIRED_BUTTON_LABEL, "scope"}]


def test_the_activity_panels_scope_line_for_a_pin_shows_none_of_them_either(
    workspace, tmp_path, monkeypatch
) -> None:
    """AC-47's activity clause. The panel renders a scope line ONLY for a PIN question —
    ``activity_scope_words`` returns ``{}`` for DEFAULT — so this run seeds one strip
    target with "Only these" on and sends through the scripted graph ``test_app_activity``
    uses, crossing the same stream → events → view path a real question does. The
    positive assertion comes first: without it a panel that rendered no line at all
    would pass.
    """
    monkeypatch.setenv("PYDOCS_CONFIG", write_config(tmp_path, model="main-a"))
    monkeypatch.setattr(agent_module, "build_agent", FakeActivityGraphBuilder(None))
    monkeypatch.setattr(reformulation_module, "reformulate", _same_question)
    at = page(
        connection_bearer=FakeBearer("tok-sentinel-abcd"),
        scope_strip=StripState(targets=(StripTarget(_PROJECT, (_BRANCH,)),), only_these=True),
    )
    at.run()
    at.chat_input[0].set_value("why does retry drop?").run()
    at.toggle(key=TECHNICAL_TOGGLE_KEY).set_value(True).run()
    assert not at.exception, at.exception
    shown = [*_every_text(at), *_shown_strings(at)]
    assert [s for s in shown if s.startswith("Searching only in: ")]  # the line IS rendered
    assert not _retired_in(shown)
