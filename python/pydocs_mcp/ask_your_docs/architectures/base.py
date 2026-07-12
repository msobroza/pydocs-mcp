"""AgentArchitecture ABC + AgentBuildContext (spec §3.2).

Light module: no langgraph/streamlit imports — those live inside the entry
modules' ``build`` methods, so importing the registry stays cheap and the
subpackage's lazy-import contract holds.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from pydocs_mcp.ask_your_docs.multimodal import ModelCapabilities
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig


class AgentArchitectureError(ValueError):
    """A selected architecture cannot be built for the detected model
    capabilities — the message carries the fix (YAML-anchored pointer)."""


@dataclass(frozen=True, slots=True)
class AgentBuildContext:
    """Ambient dependencies for architecture builders — the agent-side mirror
    of retrieval's BuildContext (retrieval/serialization.py)."""

    llm: Any  # ChatOpenAI (typed Any: the extra is mypy-excluded)
    tools: Sequence[Any]  # MCP-adapter tools from MultiServerMCPClient
    prompt: str  # SYSTEM_PROMPT + catalog listing
    capabilities: ModelCapabilities
    config: AskYourDocsConfig


def effective_tools(ctx: AgentBuildContext) -> tuple:
    """The MCP tools, plus ``reinspect_images`` when the model can see.

    The reinspect tool needs a vision-capable llm (it re-reads stored image
    bytes), so text-only builds omit it — the agent then has no dangling
    tool it can never satisfy.
    """
    if not ctx.capabilities.multimodal:
        return tuple(ctx.tools)
    from pydocs_mcp.ask_your_docs.reinspect import build_reinspect_tool

    return (
        *ctx.tools,
        build_reinspect_tool(ctx.llm, max_per_turn=ctx.config.images.max_reinspect_per_turn),
    )


class AgentArchitecture(ABC):
    """One registrable agent architecture. Entries are stateless frozen
    dataclasses; ``build`` returns a compiled LangGraph graph exposing
    ``ainvoke({"messages": [...]})`` and ``get_graph()`` (introspection
    contract — the README's agent-graph.png regeneration must keep working)."""

    #: Build-time capability requirement, validated by build_agent BEFORE
    #: building (spec §3.4.4). ClassVar metadata is the minimal extension over
    #: the ComponentRegistry precedent (which carries only the class itself).
    requires_multimodal: ClassVar[bool] = False

    @abstractmethod
    def build(self, ctx: AgentBuildContext) -> Any: ...

    # from_dict/to_dict follow the ComponentRegistry contract so
    # agent_registry.build({"type": name, ...}, ctx) works if a future spec
    # wants data-driven construction; for now entries carry no parameters.
    @classmethod
    def from_dict(cls, data: dict, context: object) -> AgentArchitecture:
        return cls()  # type: ignore[call-arg]


__all__ = ("AgentArchitecture", "AgentArchitectureError", "AgentBuildContext")
