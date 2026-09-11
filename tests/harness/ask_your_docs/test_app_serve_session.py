"""The page holds ONE serve child per browser session (AppTest over the real page).

A real UI question used to spawn up to 12 ``pydocs-mcp serve`` children — one per tool
call. The page now caches a ``PageAgentHandle`` per browser session
(``st.cache_resource(scope="session")``) and its ``on_release`` closes the child. AppTest
runs every page under ONE constant session id, so ``FakeSessionIds`` switches the id the
session-scoped cache reads to stand in for a second tab, and
``clear_session_resource_cache`` stands in for a disconnect.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

import streamlit as st
from streamlit.runtime.caching import cache_resource_api, clear_session_resource_cache

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
import pydocs_mcp.harness.ask_your_docs.reformulation as reformulation_module
from pydocs_mcp.harness.ask_your_docs.connection_dialog import STATE_OVERRIDE
from pydocs_mcp.harness.ask_your_docs.llm_connection import ConnectionOverride

from ._connection_fakes import FakeBearer

# page_env is autouse: importing it into this module is what arms it.
from ._page_fixtures import page, page_env, write_config
from ._serve_session_fakes import FakeServeToolsOpener, FakeSessionIds, wait_until

_APPTEST_SESSION_ID = "test session id"  # streamlit/testing/v1/local_script_runner.py


class _AgentStackSpy:
    """Stands in for ``build_agent`` / ``ask`` / ``reformulate``; records the bound tools."""

    def __init__(self) -> None:
        self.builds = 0
        self.tools_seen: list[list] = []

    async def build(self, *_args, mcp_tools=None, **_kwargs):
        self.builds += 1
        self.tools_seen.append(mcp_tools)
        return f"agent-{self.builds}", f"llm-{self.builds}"

    async def ask(self, *_args, **_kwargs):
        return "an answer"

    async def reformulate(self, _llm, _history, question, **_kwargs):
        return question

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(agent_module, "build_agent", self.build)
        monkeypatch.setattr(agent_module, "ask", self.ask)
        monkeypatch.setattr(reformulation_module, "reformulate", self.reformulate)


@pytest.fixture
def spy(tmp_path, monkeypatch) -> _AgentStackSpy:
    monkeypatch.setenv("PYDOCS_CONFIG", write_config(tmp_path, model="main-a"))
    agent_stack = _AgentStackSpy()
    agent_stack.install(monkeypatch)
    return agent_stack


def _page(opener: FakeServeToolsOpener, *, model: str | None = None):
    seeds = {"connection_bearer": FakeBearer(), "serve_tools_opener": opener}
    if model is not None:
        seeds[STATE_OVERRIDE] = ConnectionOverride(model=model)
    at = page(**seeds)
    at.run()
    assert not at.exception, at.exception
    return at


def _send(at, question: str = "what does Pool.acquire return?"):
    at.chat_input[0].set_value(question).run()
    assert not at.exception, at.exception
    return at


def test_two_questions_share_one_serve_child(spy: _AgentStackSpy) -> None:
    opener = FakeServeToolsOpener()
    at = _send(_send(_page(opener)), "and what does release do?")
    assert opener.opens == 1 and spy.builds == 1
    assert spy.tools_seen[0] and spy.tools_seen[0][0].session is opener.sessions[0]
    assert [text for role, text in at.session_state.messages if role == "assistant"] == [
        "an answer",
        "an answer",
    ]


def test_the_page_never_spawns_before_the_first_question(spy: _AgentStackSpy) -> None:
    opener = FakeServeToolsOpener()
    _page(opener)
    assert opener.opens == 0 and spy.builds == 0


def test_a_model_change_closes_the_old_child(spy: _AgentStackSpy) -> None:
    opener = FakeServeToolsOpener()
    _send(_page(opener))
    _send(_page(opener, model="main-b"))
    assert opener.opens == 2 and spy.builds == 2
    assert wait_until(lambda: opener.closes == 1)


def test_clearing_the_caches_closes_every_child(spy: _AgentStackSpy) -> None:
    opener = FakeServeToolsOpener()
    _send(_page(opener))
    st.cache_resource.clear()
    assert wait_until(lambda: opener.closes == opener.opens == 1)


def test_a_disconnect_closes_that_tabs_child(spy: _AgentStackSpy) -> None:
    opener = FakeServeToolsOpener()
    _send(_page(opener))
    clear_session_resource_cache(_APPTEST_SESSION_ID)  # what AppSession does on disconnect
    assert wait_until(lambda: opener.closes == 1)


def test_the_restart_notice_is_rendered(spy: _AgentStackSpy) -> None:
    opener = FakeServeToolsOpener()
    at = _send(_page(opener))
    opener.sessions[-1].die()
    _send(at, "and what does release do?")
    notices = [i.value for i in at.info if "docs server had stopped" in i.value]
    assert notices == [
        "The docs server had stopped (ClosedResourceError); started a new one for this question."
    ]
    assert opener.opens == 2 and spy.builds == 2


def test_two_browser_sessions_get_two_children(spy: _AgentStackSpy, monkeypatch) -> None:
    session_ids = FakeSessionIds("tab-a")
    monkeypatch.setattr(cache_resource_api, "get_session_id_or_throw", session_ids)
    opener = FakeServeToolsOpener()
    _send(_page(opener))
    session_ids.current = "tab-b"
    _send(_page(opener))
    assert spy.builds == 2 and opener.opens == 2
    assert opener.closes == 0  # tab B never evicts tab A's entry
