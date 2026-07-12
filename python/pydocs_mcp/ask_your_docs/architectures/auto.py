"""``auto`` — conditional hybrid routed by detection (spec §3.4.3).

Build-time routing (per (model, base_url, config) agent-cache entry), not
per-message: a fixed model's capability does not change between questions, so
routing once at build keeps the compiled graph static and ``get_graph()``
rendering meaningful. Per-message image-vs-no-image branching already lives
INSIDE each architecture (the vision node passes through on str content;
``inline`` only gets blocks when images exist).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from pydocs_mcp.ask_your_docs.architectures import agent_registry, register_architecture
from pydocs_mcp.ask_your_docs.architectures.base import (
    AgentArchitecture,
    AgentBuildContext,
)


@register_architecture("auto")
@dataclass(frozen=True, slots=True)
class AutoArchitecture(AgentArchitecture):
    # Validated at ROUTE time, not build time: auto itself builds on any model.
    requires_multimodal: ClassVar[bool] = False

    def build(self, ctx: AgentBuildContext) -> Any:
        if not ctx.capabilities.multimodal:
            return agent_registry.get("text_react")().build(ctx)  # type: ignore[misc]
        chosen = ctx.config.multimodal.preferred_architecture
        arch_cls = agent_registry.get(chosen)
        if arch_cls is None:
            raise ValueError(
                f"multimodal.preferred_architecture {chosen!r} is not a registered "
                f"architecture; known: {agent_registry.names()}"
            )
        return arch_cls().build(ctx)


__all__ = ("AutoArchitecture",)
