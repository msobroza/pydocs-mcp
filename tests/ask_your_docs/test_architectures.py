"""The three-plus-one agent architectures (spec §3.4 — AC3-AC8)."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import HumanMessage

from pydocs_mcp.ask_your_docs.architectures import AgentBuildContext, agent_registry
from pydocs_mcp.ask_your_docs.architectures.inline import _IMAGE_ANALYSIS_PROMPT_SECTION
from pydocs_mcp.ask_your_docs.multimodal import ModelCapabilities
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

from ._agent_fakes import FakeLlm, FakeVisionLlm

_CAPS_VISION = ModelCapabilities(multimodal=True, source="override")
_CAPS_TEXT = ModelCapabilities(multimodal=False, source="default")


def _ctx(llm, *, caps=_CAPS_VISION, config: AskYourDocsConfig | None = None) -> AgentBuildContext:
    return AgentBuildContext(
        llm=llm,
        tools=(),
        prompt="SYSTEM-P",
        capabilities=caps,
        config=config or AskYourDocsConfig(),
    )


def _build(name: str, llm, **kw):
    return agent_registry.get(name)().build(_ctx(llm, **kw))


def test_text_react_matches_prebuilt_path() -> None:
    """AC3: the extracted status quo — the same ainvoke message shape reaches
    the model as via create_react_agent directly (regression anchor)."""
    fake = FakeLlm()
    graph = _build("text_react", fake)
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage("hi")]}))
    assert result["messages"][-1].content == "FAKE-ANSWER"
    # One model call; the system prompt + user turn reached it.
    assert len(fake.calls) == 1
    assert any(getattr(m, "content", "") == "hi" for m in fake.calls[0])
    assert any("SYSTEM-P" in str(getattr(m, "content", "")) for m in fake.calls[0])


def test_every_architecture_renders_mermaid() -> None:
    """AC4: introspection contract — get_graph() + mermaid per entry (the
    README agent-graph.png workflow)."""
    for name in agent_registry.names():
        graph = _build(name, FakeLlm())
        mermaid = graph.get_graph().draw_mermaid()
        assert "graph" in mermaid.lower() or "-->" in mermaid, name


def test_inline_prompt_gains_image_section_text_react_does_not() -> None:
    """AC5: prompt composition is the only inline/text_react difference."""
    inline_fake, react_fake = FakeLlm(), FakeLlm()
    asyncio.run(_build("inline", inline_fake).ainvoke({"messages": [HumanMessage("q")]}))
    asyncio.run(_build("text_react", react_fake).ainvoke({"messages": [HumanMessage("q")]}))
    inline_system = str(inline_fake.calls[0][0].content)
    react_system = str(react_fake.calls[0][0].content)
    assert _IMAGE_ANALYSIS_PROMPT_SECTION.strip() in inline_system
    assert _IMAGE_ANALYSIS_PROMPT_SECTION.strip() not in react_system


def test_vision_subagent_one_vision_call_and_text_only_downstream() -> None:
    """AC6: exactly ONE vision call; the ReAct node sees a text-only message
    carrying the structured facts inside [image analysis] markers."""
    fake = FakeVisionLlm(replies=["- ERROR: KeyError 'x'\n- SYMBOL: pkg.mod.f", "done"])
    graph = _build("vision_subagent", fake)
    content = [
        {"type": "text", "text": "why does this crash?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content=content)]}))
    assert len(fake.vision_calls) == 1
    # Downstream (react) calls carry NO image blocks…
    react_calls = [msgs for msgs in fake.calls if msgs not in fake.vision_calls]
    assert react_calls, "react node never invoked"
    for msgs in react_calls:
        assert all(isinstance(getattr(m, "content", ""), str) for m in msgs)
    # …and the woven message carries the fact lines inside the markers.
    woven = "\n".join(str(m.content) for m in react_calls[0])
    assert "[image analysis]" in woven and "[/image analysis]" in woven
    assert "ERROR: KeyError 'x'" in woven and "SYMBOL: pkg.mod.f" in woven
    assert "why does this crash?" in woven
    assert result["messages"][-1].content == "done"


def test_vision_subagent_plain_text_passthrough() -> None:
    """AC7: a plain-str HumanMessage passes through with no vision call."""
    fake = FakeVisionLlm()
    graph = _build("vision_subagent", fake)
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage("plain q")]}))
    assert fake.vision_calls == []
    assert result["messages"][-1].content == "FAKE-ANSWER"


def test_auto_routes_by_capability() -> None:
    """AC8: text-only → text_react graph; vision → preferred_architecture's
    graph (asserted via graph-node names)."""
    text_nodes = set(_build("auto", FakeLlm(), caps=_CAPS_TEXT).get_graph().nodes)
    assert "vision_extract" not in text_nodes  # the plain ReAct graph
    vision_nodes = set(_build("auto", FakeVisionLlm(), caps=_CAPS_VISION).get_graph().nodes)
    assert "vision_extract" in vision_nodes  # default preferred: vision_subagent
    cfg = AskYourDocsConfig.model_validate({"multimodal": {"preferred_architecture": "inline"}})
    inline_nodes = set(
        _build("auto", FakeVisionLlm(), caps=_CAPS_VISION, config=cfg).get_graph().nodes
    )
    assert "vision_extract" not in inline_nodes  # inline == plain ReAct shape
