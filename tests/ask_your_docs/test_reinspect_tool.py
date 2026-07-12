"""reinspect_images — the agent-local tool that re-contextualizes previously
attached images against the NEWEST question (session store + one selective
vision call). NOT an MCP tool: the six-tool surface is untouched."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import HumanMessage

from pydocs_mcp.ask_your_docs.agent import _active_image_store, ask
from pydocs_mcp.ask_your_docs.attachments import ImageAttachment
from pydocs_mcp.ask_your_docs.reinspect import build_reinspect_tool

from ._agent_fakes import FakeVisionLlm


def _att(name: str) -> ImageAttachment:
    return ImageAttachment(name=name, media_type="image/png", data_b64="QUFB")


def _run_tool(tool, **kwargs) -> str:
    return asyncio.run(tool.coroutine(**kwargs))


def test_reinspects_only_the_selected_images() -> None:
    """The newest question drives ONE vision call over ONLY the named images."""
    fake = FakeVisionLlm(replies=["- ERROR: Timeout in retry loop"])
    tool = build_reinspect_tool(fake)
    store = {"a.png": _att("a.png"), "b.png": _att("b.png"), "c.png": _att("c.png")}
    token = _active_image_store.set(store)
    try:
        out = _run_tool(tool, names=["a.png", "c.png"], question="what timed out?")
    finally:
        _active_image_store.reset(token)
    assert "ERROR: Timeout in retry loop" in out
    assert len(fake.vision_calls) == 1
    blocks = fake.vision_calls[0][0].content
    image_blocks = [b for b in blocks if b["type"] == "image_url"]
    assert len(image_blocks) == 2  # a.png + c.png only — b.png not re-sent
    text_block = next(b["text"] for b in blocks if b["type"] == "text")
    assert "what timed out?" in text_block


def test_unknown_name_returns_actionable_text_not_exception() -> None:
    """Tool errors are model-facing text listing the stored names."""
    fake = FakeVisionLlm()
    tool = build_reinspect_tool(fake)
    token = _active_image_store.set({"a.png": _att("a.png")})
    try:
        out = _run_tool(tool, names=["nope.png"], question="q")
    finally:
        _active_image_store.reset(token)
    assert "nope.png" in out and "a.png" in out
    assert fake.vision_calls == []


def test_empty_store_returns_helpful_message() -> None:
    fake = FakeVisionLlm()
    tool = build_reinspect_tool(fake)
    token = _active_image_store.set({})
    try:
        out = _run_tool(tool, names=["a.png"], question="q")
    finally:
        _active_image_store.reset(token)
    assert "no previously attached images" in out.lower()
    assert fake.vision_calls == []


def test_ask_pins_the_session_store_to_the_contextvar() -> None:
    """ask() scopes the store per question — the cached cross-session agent
    reads each session's own snapshot (the _active_scope pattern)."""

    class _StoreProbeAgent:
        def __init__(self) -> None:
            self.seen: object = "unset"

        async def ainvoke(self, payload: dict) -> dict:
            from langchain_core.messages import AIMessage

            self.seen = _active_image_store.get()
            return {"messages": [AIMessage("ok")]}

    probe = _StoreProbeAgent()
    store = {"x.png": _att("x.png")}
    asyncio.run(ask(probe, [], "q", image_store=store))
    assert probe.seen == store
    assert _active_image_store.get() is None  # reset after the turn


def test_architectures_expose_reinspect_tool_on_vision_models() -> None:
    """Vision-capable builds carry the tool (a 'tools' node exists even with
    zero MCP tools); text-only builds don't."""
    from langgraph.graph import MessagesState

    from pydocs_mcp.ask_your_docs.architectures import AgentBuildContext, agent_registry
    from pydocs_mcp.ask_your_docs.multimodal import ModelCapabilities
    from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

    def build(name: str, multimodal: bool):
        ctx = AgentBuildContext(
            llm=FakeVisionLlm(),
            tools=(),
            prompt="P",
            capabilities=ModelCapabilities(multimodal=multimodal, source="override"),
            config=AskYourDocsConfig(),
        )
        return agent_registry.get(name)().build(ctx)

    assert "tools" in set(build("text_react", True).get_graph().nodes)
    assert "tools" not in set(build("text_react", False).get_graph().nodes)
    assert "tools" in set(build("inline", True).get_graph().nodes)
