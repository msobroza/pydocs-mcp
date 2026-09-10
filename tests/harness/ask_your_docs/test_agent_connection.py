"""build_agent × the LLM connection (design §4.5, §4.7, §4.8 — AC-17, AC-18 gate rows, AC-34,
AC-42). Fake MCP client + fake graph builder, as in test_prompt_seam.py."""

from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

from pydocs_mcp.harness.ask_your_docs import agent as agent_mod
from pydocs_mcp.harness.ask_your_docs.architectures import AgentArchitectureError
from pydocs_mcp.harness.ask_your_docs.multimodal import CapabilitySource, ModelCapabilities
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

from ._agent_fakes import FakeLlm, FakeVisionLlm

_SEES = ModelCapabilities(True, CapabilitySource.CONFIGURED)
_BLIND = ModelCapabilities(False, CapabilitySource.CONFIGURED)


def _build(name: str, **kw):
    return agent_mod._build_architecture(
        name, llm=FakeLlm(), tools=(), prompt="P", config=AskYourDocsConfig(), model="main-a", **kw
    )


def test_explicit_inline_under_a_separate_vision_model_is_rejected() -> None:
    """AC-18 / E13: inline routes images to the main model, which is blind under SEPARATE_MODEL."""
    with pytest.raises(AgentArchitectureError) as excinfo:
        _build("inline", capabilities=_BLIND, vision_llm=FakeVisionLlm(), vision_capabilities=_SEES)
    assert "ask_your_docs.llm.vision" in str(excinfo.value) and "'main-a'" in str(excinfo.value)


def test_vision_route_gate_checks_the_image_model() -> None:
    """AC-18 / E13: vision_subagent on a text-only separate vision model is rejected naming
    vision.model; on a seeing one it builds even though the main model is blind."""
    with pytest.raises(AgentArchitectureError, match="vision.model is text-only"):
        _build(
            "vision_subagent",
            capabilities=_BLIND,
            vision_llm=FakeVisionLlm(),
            vision_capabilities=_BLIND,
        )
    graph = _build(
        "vision_subagent",
        capabilities=_BLIND,
        vision_llm=FakeVisionLlm(),
        vision_capabilities=_SEES,
    )
    assert "vision_extract" in set(graph.get_graph().nodes)


def test_text_react_never_checks_capabilities() -> None:
    graph = _build("text_react", capabilities=_BLIND)
    assert "vision_extract" not in set(graph.get_graph().nodes)


def test_context_defaults_survive_the_build_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Null Objects reach the context THROUGH _build_architecture: omitting the three
    connection keywords must not plant a None where a model / capability / bearer belongs
    (the context's own defaults are the single source of that policy)."""
    from pydocs_mcp.harness.ask_your_docs.architectures import AgentBuildContext
    from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer

    built: list[AgentBuildContext] = []

    def _recording_context(**kwargs) -> AgentBuildContext:
        built.append(AgentBuildContext(**kwargs))
        return built[-1]

    monkeypatch.setattr(agent_mod, "AgentBuildContext", _recording_context)
    _build("text_react", capabilities=_SEES)
    (ctx,) = built
    assert ctx.vision_llm is ctx.llm
    assert ctx.vision_capabilities is _SEES
    assert isinstance(ctx.bearer, NoBearer)
