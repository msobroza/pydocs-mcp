"""The three-plus-one agent architectures (spec §3.4 — AC3-AC8)."""

from __future__ import annotations

import asyncio
import logging

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import HumanMessage

from pydocs_mcp.harness.ask_your_docs.architectures import AgentBuildContext, agent_registry
from pydocs_mcp.harness.ask_your_docs.architectures.inline import _IMAGE_ANALYSIS_PROMPT_SECTION
from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities
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
    """AC8 + design R6: text-only → text_react graph; vision → preferred_architecture's graph
    (the shipped `inline` default, or an explicit override)."""
    text_fake, vision_fake = FakeLlm(), FakeVisionLlm()
    text_graph = _build("auto", text_fake, caps=_CAPS_TEXT)
    vision_graph = _build("auto", vision_fake, caps=_CAPS_VISION)
    assert "vision_extract" not in set(text_graph.get_graph().nodes)  # the plain ReAct graph
    assert "vision_extract" not in set(vision_graph.get_graph().nodes)  # inline == ReAct shape
    # 2026-09-05: the shipped default is inline, whose graph is ALSO the plain ReAct
    # shape — so node names cannot tell the two routes apart. The image-analysis
    # prompt section is the discriminator (same idea as the AC5 test above): without
    # it, a bug that always routed to text_react would pass this test.
    asyncio.run(text_graph.ainvoke({"messages": [HumanMessage("q")]}))
    asyncio.run(vision_graph.ainvoke({"messages": [HumanMessage("q")]}))
    assert _IMAGE_ANALYSIS_PROMPT_SECTION.strip() not in str(text_fake.calls[0][0].content)
    assert _IMAGE_ANALYSIS_PROMPT_SECTION.strip() in str(vision_fake.calls[0][0].content)
    cfg = AskYourDocsConfig.model_validate(
        {"multimodal": {"preferred_architecture": "vision_subagent"}}
    )
    subagent_nodes = set(
        _build("auto", FakeVisionLlm(), caps=_CAPS_VISION, config=cfg).get_graph().nodes
    )
    assert "vision_extract" in subagent_nodes  # the override reaches the extraction graph


def test_auto_routes_a_separate_vision_model_to_vision_subagent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC-18: a separate vision model always builds vision_subagent; preferred inline is
    overridden with one auto_routing log (E12), preferred vision_subagent logs nothing."""
    caplog.set_level(logging.INFO)

    def _separate(config: AskYourDocsConfig) -> set[str]:
        ctx = AgentBuildContext(
            llm=FakeLlm(),
            tools=(),
            prompt="P",
            capabilities=_CAPS_TEXT,
            config=config,
            vision_llm=FakeVisionLlm(),
            vision_capabilities=_CAPS_VISION,
        )
        return set(agent_registry.get("auto")().build(ctx).get_graph().nodes)

    assert "vision_extract" in _separate(AskYourDocsConfig())  # preferred inline → re-routed
    routed = [r.getMessage() for r in caplog.records if "auto_routing" in r.getMessage()]
    assert len(routed) == 1 and '"built": "vision_subagent"' in routed[0]
    caplog.clear()
    cfg = AskYourDocsConfig.model_validate(
        {"multimodal": {"preferred_architecture": "vision_subagent"}}
    )
    assert "vision_extract" in _separate(cfg)
    assert not [r for r in caplog.records if "auto_routing" in r.getMessage()]


# ── LLM-connection design §4.8: the image-model route ──


def test_context_defaults_are_identity_and_the_null_object() -> None:
    from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer

    ctx = _ctx(FakeLlm())
    assert ctx.vision_llm is ctx.llm
    assert ctx.vision_capabilities == ctx.capabilities
    assert isinstance(ctx.bearer, NoBearer)


def _split_model_ctx(main, vision) -> AgentBuildContext:
    """A separate vision model: a text-only main model beside a vision-capable image model."""
    return AgentBuildContext(
        llm=main,
        tools=(),
        prompt="SYSTEM-P",
        capabilities=_CAPS_TEXT,
        config=AskYourDocsConfig(),
        vision_llm=vision,
        vision_capabilities=_CAPS_VISION,
    )


def _graph_bound_reinspect_tool(graph):
    """The reinspect tool the COMPILED graph carries, so the assertion pins what ``build``
    wired rather than what ``effective_tools`` returns when a test calls it directly."""
    nodes = graph.get_graph(xray=True).nodes
    # A build() that passed the MAIN route attaches no tool at all (the main model is
    # text-only), and create_react_agent then compiles no tool node — so this fails first.
    assert "react_agent:tools" in nodes, list(nodes)
    return nodes["react_agent:tools"].data.tools_by_name["reinspect_images"]


def _run_reinspect(tool) -> str:
    """Invoke a bound reinspect tool with the per-turn contextvars pinned (the shape the
    reinspect tests use), so the reply proves WHICH model the tool is bound to."""
    from pydocs_mcp.harness.ask_your_docs.agent import _active_image_store, _reinspect_state
    from pydocs_mcp.harness.ask_your_docs.attachments import ImageAttachment

    store = {"a.png": ImageAttachment(name="a.png", media_type="image/png", data_b64="QUFB")}
    tokens = _active_image_store.set(store), _reinspect_state.set({"calls": 0, "memo": {}})
    try:
        return asyncio.run(tool.coroutine(names=["a.png"], question="what failed?"))
    finally:
        _active_image_store.reset(tokens[0])
        _reinspect_state.reset(tokens[1])


def test_vision_subagent_routes_images_to_the_vision_model() -> None:
    """A separate vision model: the vision node AND the reinspect tool the graph bound both
    call it; the ReAct loop stays on the (text-only) main model and sees no image block."""
    main = FakeLlm(replies=["done"])
    vision = FakeVisionLlm(replies=["- PATH: a/b.py", "- SYMBOL: pkg.mod.f"])
    graph = agent_registry.get("vision_subagent")().build(_split_model_ctx(main, vision))
    # Pin build()'s route choice: the tool inside the compiled graph calls the VISION fake.
    assert _run_reinspect(_graph_bound_reinspect_tool(graph)) == "- PATH: a/b.py"
    assert len(vision.vision_calls) == 1 and main.calls == []
    content = [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content=content)]}))
    assert result["messages"][-1].content == "done"
    assert len(vision.vision_calls) == 2  # + the extraction node's own image call
    assert main.calls and not any(
        not isinstance(getattr(m, "content", ""), str) for msgs in main.calls for m in msgs
    )
    assert "SYMBOL: pkg.mod.f" in "\n".join(str(m.content) for m in main.calls[0])


def test_vision_route_binds_the_reinspect_tool_to_the_vision_model() -> None:
    """``effective_tools``' own contract: the reinspect tool re-reads stored image bytes, so on
    the VISION route it is gated on the vision model's capability AND bound to that model."""
    from pydocs_mcp.harness.ask_your_docs.architectures import ImageModelRoute
    from pydocs_mcp.harness.ask_your_docs.architectures.base import effective_tools

    main, vision = FakeLlm(), FakeVisionLlm(replies=["- SYMBOL: pkg.mod.f"])
    ctx = _split_model_ctx(main, vision)
    assert agent_registry.get("vision_subagent")().image_model_route is ImageModelRoute.VISION
    assert effective_tools(ctx) == ()  # the MAIN route reads the text-only main capability
    (tool,) = effective_tools(ctx, ImageModelRoute.VISION)
    assert tool.name == "reinspect_images"
    assert _run_reinspect(tool) == "- SYMBOL: pkg.mod.f"
    assert len(vision.vision_calls) == 1 and main.calls == []


def test_vision_node_lets_a_provider_failure_propagate() -> None:
    """The person attached the image on purpose: the node does not swallow the failure (the
    app's send-loop boundary renders it redacted)."""

    class _Failing(FakeVisionLlm):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("upstream rejected Bearer tok-one-abcd")

    graph = _build("vision_subagent", _Failing())
    content = [
        {"type": "text", "text": "q"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]
    with pytest.raises(RuntimeError, match="upstream rejected"):
        asyncio.run(graph.ainvoke({"messages": [HumanMessage(content=content)]}))
