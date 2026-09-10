"""``auto`` — conditional hybrid routed by detection (spec §3.4.3; design §4.7).

Build-time routing (per (model, base_url, config) agent-cache entry), not
per-message: a fixed model's capability does not change between questions, so
routing once at build keeps the compiled graph static and ``get_graph()``
rendering meaningful. Per-message image-vs-no-image branching already lives
INSIDE each architecture (the vision node passes through on str content;
``inline`` only gets blocks when images exist).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, ClassVar

from pydocs_mcp.harness.ask_your_docs.architectures import agent_registry, register_architecture
from pydocs_mcp.harness.ask_your_docs.architectures.base import (
    AgentArchitecture,
    AgentBuildContext,
)

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

# The only architecture that can send image blocks to a SECOND model: its
# extraction node calls ctx.vision_llm while the ReAct loop stays on ctx.llm.
_VISION_SUBAGENT = "vision_subagent"


@register_architecture("auto")
@dataclass(frozen=True, slots=True)
class AutoArchitecture(AgentArchitecture):
    # auto only picks rows whose image model can see; the gate covers explicit selections.
    requires_multimodal: ClassVar[bool] = False
    routes_to_another_architecture: ClassVar[bool] = True

    def build(self, ctx: AgentBuildContext) -> Any:
        """The §4.7 routing table, read top-down: a separate vision model decides
        first, so the remaining rows may read ``ctx.capabilities`` — with one model
        the main verdict IS the image model's verdict."""
        chosen = ctx.config.multimodal.preferred_architecture
        if ctx.has_separate_vision_model:
            _log_separate_model_reroute(chosen)
            return agent_registry.get(_VISION_SUBAGENT)().build(ctx)  # type: ignore[misc]
        if not ctx.capabilities.multimodal:
            return agent_registry.get("text_react")().build(ctx)  # type: ignore[misc]
        return _preferred_architecture(chosen)().build(ctx)


def _log_separate_model_reroute(preferred: str) -> None:
    """Design E12: say once, at build time, that a separate vision model overrode the
    preference — every architecture but ``vision_subagent`` routes images to the main
    model, which is text-only by construction under that rule."""
    if preferred == _VISION_SUBAGENT:
        return
    log.info(
        json.dumps(
            {
                "event": "auto_routing",
                "preferred": preferred,
                "built": _VISION_SUBAGENT,
                "reason": f"{preferred} cannot route images to a separate vision model",
            }
        )
    )


def _preferred_architecture(chosen: str) -> type[AgentArchitecture]:
    """The class named by ``multimodal.preferred_architecture`` — free-form YAML text, so an
    unknown name, or one that delegates instead of building, fails loudly carrying the
    offending value and the set it could have named."""
    arch_cls = agent_registry.get(chosen)
    if arch_cls is None:
        raise ValueError(
            f"multimodal.preferred_architecture {chosen!r} is not a registered "
            f"architecture; known: {agent_registry.names()}"
        )
    if arch_cls.routes_to_another_architecture:
        # Naming the delegator here would send it straight back into this row (a vision-capable
        # single model) and recurse until the stack ran out — on THAT deployment only.
        raise ValueError(
            f"multimodal.preferred_architecture {chosen!r} routes to another architecture and "
            f"cannot be a routing target; name one of {_concrete_architecture_names()}"
        )
    return arch_cls


def _concrete_architecture_names() -> list[str]:
    """The registered architectures that build a graph of their own."""
    return [
        name
        for name in agent_registry.names()
        if not agent_registry.get(name).routes_to_another_architecture  # type: ignore[union-attr]
    ]


__all__ = ("AutoArchitecture",)
