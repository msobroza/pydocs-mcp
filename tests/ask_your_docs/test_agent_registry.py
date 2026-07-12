"""agent_registry contracts (spec §3.3 / §3.4.4 — AC1, AC2, AC9)."""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")

from pydocs_mcp.ask_your_docs.architectures import (
    AgentArchitecture,
    agent_registry,
)
from pydocs_mcp.ask_your_docs.multimodal import ModelCapabilities
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig
from pydocs_mcp.retrieval.serialization import ComponentRegistry


def test_registry_names_exactly_the_four() -> None:
    """AC1: the enumeration surface future specs iterate over."""
    assert agent_registry.names() == ("auto", "inline", "text_react", "vision_subagent")


def test_get_unknown_returns_none_and_build_raises_listing_names() -> None:
    """AC1: unknown-name failure modes."""
    from pydocs_mcp.ask_your_docs.agent import _build_architecture

    assert agent_registry.get("nope") is None
    with pytest.raises(ValueError, match="text_react"):
        _build_architecture(
            "nope",
            llm=None,
            tools=(),
            prompt="p",
            capabilities=ModelCapabilities(True, "override"),
            config=AskYourDocsConfig(),
            model="m",
        )


def test_duplicate_registration_raises_at_decoration_time() -> None:
    """AC2: wiring bugs surface at import time (ComponentRegistry contract),
    demonstrated on a scratch instance so the real registry stays clean."""
    scratch: ComponentRegistry[AgentArchitecture] = ComponentRegistry()

    @scratch.register("dup")
    class _A(AgentArchitecture):
        def build(self, ctx) -> object:
            return object()

    with pytest.raises(ValueError, match="already registered"):

        @scratch.register("dup")
        class _B(AgentArchitecture):
            def build(self, ctx) -> object:
                return object()


def test_multimodal_required_raises_actionable_error() -> None:
    """AC9: requires_multimodal + text-only detection → AgentArchitectureError
    naming the model, the source, and the override YAML path."""
    from pydocs_mcp.ask_your_docs.agent import _build_architecture
    from pydocs_mcp.ask_your_docs.architectures import AgentArchitectureError

    with pytest.raises(AgentArchitectureError) as excinfo:
        _build_architecture(
            "inline",
            llm=None,
            tools=(),
            prompt="p",
            capabilities=ModelCapabilities(multimodal=False, source="static"),
            config=AskYourDocsConfig(),
            model="gpt-3.5-turbo",
        )
    msg = str(excinfo.value)
    assert "gpt-3.5-turbo" in msg
    assert "source=static" in msg
    assert "ask_your_docs.multimodal.detection.override" in msg
