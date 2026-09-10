"""AgentArchitecture ABC + AgentBuildContext (spec §3.2; LLM-connection design §4.8).

Light module: no langgraph/streamlit imports — those live inside the entry
modules' ``build`` methods, so importing the registry stays cheap and the
subpackage's lazy-import contract holds.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import BearerSource, NoBearer
from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig


class AgentArchitectureError(ValueError):
    """A selected architecture cannot be built for the detected model
    capabilities — the message carries the fix (YAML-anchored pointer)."""


class ImageModelRoute(StrEnum):
    """Which model an architecture sends image blocks to (design §4.8)."""

    MAIN = "main"  # ctx.llm
    VISION = "vision"  # ctx.vision_llm (= ctx.llm unless a separate vision model is configured)


# Construction-time marker for "this field mirrors the main model", resolved in
# __post_init__. The fields are NEVER None afterwards, so no consumer carries an
# `is None` guard (the repository's Null Object rule).
_INHERIT_FROM_MAIN: Any = object()


@dataclass(frozen=True, slots=True)
class AgentBuildContext:
    """Ambient dependencies for architecture builders — the agent-side mirror
    of retrieval's BuildContext (retrieval/serialization.py)."""

    llm: Any  # ChatOpenAI (typed Any: the extra is mypy-excluded)
    tools: Sequence[Any]  # MCP-adapter tools from MultiServerMCPClient
    prompt: str  # SYSTEM_PROMPT + catalog listing
    capabilities: ModelCapabilities
    config: AskYourDocsConfig
    vision_llm: Any = _INHERIT_FROM_MAIN  # the image model; default = llm
    vision_capabilities: ModelCapabilities = _INHERIT_FROM_MAIN  # default = capabilities
    bearer: BearerSource = field(default_factory=NoBearer)  # redacts a tool result (H4)

    def __post_init__(self) -> None:
        # The identity defaults resolve HERE on a frozen, slotted dataclass, so
        # every existing five-field construction site keeps working unchanged.
        if self.vision_llm is _INHERIT_FROM_MAIN:
            object.__setattr__(self, "vision_llm", self.llm)
        if self.vision_capabilities is _INHERIT_FROM_MAIN:
            object.__setattr__(self, "vision_capabilities", self.capabilities)


def effective_tools(ctx: AgentBuildContext, route: ImageModelRoute = ImageModelRoute.MAIN) -> tuple:
    """The MCP tools, plus ``reinspect_images`` when the EFFECTIVE image model can see.

    The tool re-reads stored image bytes, so it is gated on the capability of
    the model that will actually see them and bound to that same model:
    ``ctx.vision_llm`` on the VISION route, ``ctx.llm`` on MAIN. Without a
    separate vision model both resolve to the same objects, so text-only builds
    omit the tool exactly as before.
    """
    on_vision_route = route is ImageModelRoute.VISION
    image_caps = ctx.vision_capabilities if on_vision_route else ctx.capabilities
    if not image_caps.multimodal:
        return tuple(ctx.tools)
    from pydocs_mcp.harness.ask_your_docs.reinspect import build_reinspect_tool

    image_llm = ctx.vision_llm if on_vision_route else ctx.llm
    return (
        *ctx.tools,
        build_reinspect_tool(
            image_llm, max_per_turn=ctx.config.images.max_reinspect_per_turn, bearer=ctx.bearer
        ),
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

    #: Which model this architecture sends image blocks to; the requirement
    #: above is checked against THAT model's capabilities (design §4.8).
    image_model_route: ClassVar[ImageModelRoute] = ImageModelRoute.MAIN

    #: The registry name — set by @register_architecture, which also binds
    #: the prompt namespace (prompts/<architecture_name>/ with shared/
    #: fallback). Never set this by hand; the decorator is the single wiring.
    architecture_name: ClassVar[str]

    @classmethod
    def prompts(cls):
        """This architecture's prompt namespace (convention: its registry
        name IS its prompt directory; shared/ serves everything else)."""
        from pydocs_mcp.harness.ask_your_docs.prompts import prompts_for

        return prompts_for(cls.architecture_name)

    @abstractmethod
    def build(self, ctx: AgentBuildContext) -> Any: ...

    # from_dict/to_dict follow the ComponentRegistry contract so
    # agent_registry.build({"type": name, ...}, ctx) works if a future spec
    # wants data-driven construction; for now entries carry no parameters.
    @classmethod
    def from_dict(cls, data: dict, context: object) -> AgentArchitecture:
        return cls()  # type: ignore[call-arg]


__all__ = ("AgentArchitecture", "AgentArchitectureError", "AgentBuildContext", "ImageModelRoute")
