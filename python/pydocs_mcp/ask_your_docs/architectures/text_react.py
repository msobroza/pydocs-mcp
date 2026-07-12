"""``text_react`` — the extracted status quo (spec §3.4.0).

Exactly the pre-registry ``create_react_agent(llm, tools, prompt=prompt)``
body from agent.py, moved behind the registry. This is the back-compat
anchor: with no config and no images, behavior is byte-identical (AC3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from pydocs_mcp.ask_your_docs.architectures import agent_registry
from pydocs_mcp.ask_your_docs.architectures.base import (
    AgentArchitecture,
    AgentBuildContext,
)


@agent_registry.register("text_react")
@dataclass(frozen=True, slots=True)
class TextReactArchitecture(AgentArchitecture):
    requires_multimodal: ClassVar[bool] = False

    def build(self, ctx: AgentBuildContext) -> Any:
        # WHY lazy import: langgraph is [ask-your-docs]-extra weight; the
        # registry module must stay importable core-only. create_react_agent
        # is deprecated-but-working in the locked langgraph-prebuilt (moved
        # upstream to langchain.agents); migrating is out of scope — the repo
        # used it before this refactor too.
        from langgraph.prebuilt import create_react_agent

        return create_react_agent(ctx.llm, ctx.tools, prompt=ctx.prompt)


__all__ = ("TextReactArchitecture",)
