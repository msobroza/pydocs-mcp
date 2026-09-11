"""The page's four cached seams, watched from outside the page (design §4.7, §4.9).

``get_capabilities``, ``get_agent``, ``page_bearer`` and ``connection_key`` had ZERO test
references: every AC that names them was pinned through the things AROUND them — the status
line, the dialog, the send loop — so the caches themselves (how often the ladder runs, what
a rebuild costs, which bearer the page holds) were free to change unnoticed.

Each test drives the REAL page through AppTest and watches a named spy: what the page called,
how many times, and with which object. Streamlit's ``cache_resource`` is process-global and
cleared per test by ``page_env``, so a second ``AppTest`` in one test still sees the first
one's cached entries — which is how the Refresh / Renew legs avoid chaining a run onto a
dialog click that ends in ``st.rerun()``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
import pydocs_mcp.harness.ask_your_docs.llm_connection as llm_connection
import pydocs_mcp.harness.ask_your_docs.reformulation as reformulation_module
from pydocs_mcp.harness.ask_your_docs.connection_dialog import (
    KEY_REFRESH,
    KEY_RENEW,
    STATE_OVERRIDE,
    TOKEN_UNAVAILABLE,
)
from pydocs_mcp.harness.ask_your_docs.chat_wire import WireParams
from pydocs_mcp.harness.ask_your_docs.llm_connection import ConnectionOverride
from pydocs_mcp.harness.ask_your_docs.multimodal import CapabilitySource, ModelCapabilities
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ChatParamsConfig

from ._connection_fakes import FakeBearer

# page_env is autouse: importing it into this module is what arms it.
from ._page_fixtures import open_dialog, page, page_env, status_line, write_config

_QUESTION = "what does Pool.acquire return?"
_BASE_URL = "https://llm.internal/v1"  # write_config's default endpoint


def _override(*, model: str = "main-a", base_url: str | None = None) -> dict:
    """A session-state seed for one dialog override — the page's third resolution tier."""
    return {STATE_OVERRIDE: ConnectionOverride(base_url=base_url, model=model)}


class _CapabilitySpy:
    """Stands in for ``resolve_vision_capabilities``: counts calls, answers one fixed pair."""

    def __init__(self) -> None:
        self.calls = 0
        self.seen: list[tuple[str | None, str | None]] = []

    async def __call__(self, connection, _bearer, _detection):
        self.calls += 1
        self.seen.append((connection.model, connection.base_url))
        verdict = ModelCapabilities(True, CapabilitySource.STATIC)
        return verdict, verdict


class _AgentSpy:
    """Stands in for ``build_agent`` / ``ask`` / ``reformulate``: one sentinel agent per build.

    The sentinel is what makes "the same cached agent" checkable from outside the page —
    ``ask`` records the object it was handed, so a rebuild shows up as a new name.
    """

    def __init__(self) -> None:
        self.builds = 0
        self.agents_asked: list[str] = []
        self.wires: list = []

    async def build(self, *_args, **kwargs):
        self.builds += 1
        self.wires.append(kwargs.get("wire"))
        return f"agent-{self.builds}", f"llm-{self.builds}"

    async def ask(self, agent, *_args, **_kwargs):
        self.agents_asked.append(agent)
        return "an answer"

    async def reformulate(self, _llm, _history, question, **_kwargs):
        return question

    def install(self, monkeypatch) -> None:
        """AppTest re-executes app.py per run, so its ``from … import`` picks these up."""
        monkeypatch.setattr(agent_module, "build_agent", self.build)
        monkeypatch.setattr(agent_module, "ask", self.ask)
        monkeypatch.setattr(reformulation_module, "reformulate", self.reformulate)


class _RegistrySpy:
    """Stands in for ``bearer_for_connection``: records the connections it was asked about."""

    def __init__(self, bearer) -> None:
        self.bearer = bearer
        self.connections: list = []

    def __call__(self, connection):
        self.connections.append(connection)
        return self.bearer


def _run(at):
    at.run()
    assert not at.exception, at.exception
    return at


def _send(at, question: str = _QUESTION):
    at.chat_input[0].set_value(question).run()
    assert not at.exception, at.exception
    return at


def test_one_capability_resolution_serves_every_reader_of_a_key(tmp_path, monkeypatch) -> None:
    """AC-17 (app half): the status line, the send policy and the agent read ONE verdict pair.

    ``get_capabilities`` is the single call site — three readers per render must not become
    three ladder runs — and its ``ConnectionKey`` is what a second render hits or misses on.
    """
    monkeypatch.setenv("PYDOCS_CONFIG", write_config(tmp_path, model="main-a"))
    spy = _CapabilitySpy()
    monkeypatch.setattr(llm_connection, "resolve_vision_capabilities", spy)
    at = page(connection_bearer=FakeBearer())
    at.run()
    assert not at.exception, at.exception
    assert spy.calls == 1
    at.run()  # a second render of the same connection
    assert spy.calls == 1  # ... served from the cache, not the ladder
    _run(page(connection_bearer=FakeBearer(), **_override(model="main-b")))
    assert spy.calls == 2  # the key carries the model …
    _run(page(connection_bearer=FakeBearer(), **_override(base_url="http://other/v1")))
    assert spy.calls == 3  # … and the endpoint
    assert spy.seen == [("main-a", _BASE_URL), ("main-b", _BASE_URL), ("main-a", "http://other/v1")]


def test_no_model_chosen_never_builds_an_agent(tmp_path, monkeypatch) -> None:
    """AC-25 (f): with no model anywhere, the send refuses BEFORE ``get_agent`` (design E19).

    An agent build spawns the pydocs-mcp subprocess and constructs the chat model, so
    "the page refuses" has to mean it never got that far — not that it recovered afterwards.
    """
    monkeypatch.setenv("PYDOCS_CONFIG", write_config(tmp_path))
    spy = _AgentSpy()
    spy.install(monkeypatch)
    at = page(connection_bearer=FakeBearer())
    at.run()
    _send(at)
    assert spy.builds == 0 and spy.agents_asked == []
    assert any("No model chosen" in e.value for e in at.error)


def test_an_unavailable_bearer_never_builds_an_agent(tmp_path, monkeypatch) -> None:
    """AC-44 (b): ``token unavailable ⚠`` on the status line AND ``get_agent`` uncalled."""
    monkeypatch.setenv("PYDOCS_CONFIG", write_config(tmp_path, model="main-a"))
    spy = _AgentSpy()
    spy.install(monkeypatch)
    at = page(connection_bearer=FakeBearer(fail=True))
    at.run()
    assert not at.exception, at.exception
    assert TOKEN_UNAVAILABLE in status_line(at)
    _send(at)
    assert spy.builds == 0 and spy.agents_asked == []


def test_the_cached_agent_outlives_a_refresh_and_a_renew(tmp_path, monkeypatch) -> None:
    """AC-26 + AC-45: one build per ``ConnectionKey``; neither dialog button rebuilds it.

    The key projects the endpoint, the model, the config path and the auth IDENTITY — never
    the token — so a Renew mutates the bearer's cache and leaves this entry alone (R7). Each
    click gets its own AppTest because the handlers end in ``st.rerun()``; the page cache is
    process-global, so the third page still meets the first page's agent.
    """
    monkeypatch.setenv("PYDOCS_CONFIG", write_config(tmp_path, model="main-a"))
    spy = _AgentSpy()
    spy.install(monkeypatch)
    bearer = FakeBearer("tok-one-abcd")
    _send(_run(page(connection_bearer=bearer)))
    assert spy.builds == 1 and spy.agents_asked == ["agent-1"]

    refreshed = open_dialog(page(connection_bearer=bearer))
    refreshed.button(key=KEY_REFRESH).click().run()
    assert not refreshed.exception, refreshed.exception

    renewed = open_dialog(page(connection_bearer=bearer))
    renewed.button(key=KEY_RENEW).click().run()
    assert not renewed.exception, renewed.exception
    assert bearer.renewals == 1

    _send(_run(page(connection_bearer=bearer)), "and what does release do?")
    assert spy.builds == 1  # one build for the key, through both clicks
    assert spy.agents_asked == ["agent-1", "agent-1"]


def test_two_params_snapshots_build_two_page_agents(tmp_path, monkeypatch) -> None:
    """Model-params v2 §5 rule 8: the agent's key carries the hashable wire, so different
    settings build different agents — and the same settings, sent twice, share one."""
    monkeypatch.setenv("PYDOCS_CONFIG", write_config(tmp_path, model="main-a"))
    spy = _AgentSpy()
    spy.install(monkeypatch)
    bearer = FakeBearer("tok-one-abcd")
    for temperature in (0.2, 0.2, 0.7):
        params = ChatParamsConfig(temperature=temperature)
        seeds = {STATE_OVERRIDE: ConnectionOverride(model="main-a", params=params)}
        _send(_run(page(connection_bearer=bearer, **seeds)))
    assert spy.builds == 2
    assert spy.wires == [
        WireParams((("temperature", 0.2),)),
        WireParams((("temperature", 0.7),)),
    ]


def test_page_bearer_falls_back_to_the_registry(tmp_path, monkeypatch) -> None:
    """AC-34 / AC-40 (app leg): no ``connection_bearer`` seam ⇒ the REGISTRY's object.

    Every existing page test seeds that seam, which shadows the registry branch entirely —
    so the page could have built a private bearer per render (a token fetch per render, and
    a Renew that renews something nobody presents) with the whole suite still green.
    """
    monkeypatch.setenv("PYDOCS_CONFIG", write_config(tmp_path, model="main-a"))
    spy = _RegistrySpy(FakeBearer("tok-registry-7777"))
    monkeypatch.setattr(llm_connection, "bearer_for_connection", spy)
    at = _run(page())  # no seam seeded
    assert "…7777" in status_line(at)
    assert spy.connections and spy.connections[0].model == "main-a"


def test_a_seeded_bearer_shadows_the_registry(tmp_path, monkeypatch) -> None:
    """The other leg of ``page_bearer``: the AppTest seam wins and the registry is not asked."""
    monkeypatch.setenv("PYDOCS_CONFIG", write_config(tmp_path, model="main-a"))
    spy = _RegistrySpy(FakeBearer("tok-registry-7777"))
    monkeypatch.setattr(llm_connection, "bearer_for_connection", spy)
    at = _run(page(connection_bearer=FakeBearer("tok-seeded-4321")))
    assert "…4321" in status_line(at)
    assert spy.connections == []
