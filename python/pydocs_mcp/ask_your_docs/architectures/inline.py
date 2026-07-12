"""``inline`` — one multimodal HumanMessage to the main ReAct agent (spec §3.4.1).

The graph is today's ReAct agent with an image-analysis prompt section; what
changes is message construction in ``ask()`` — when images are present the
HumanMessage content becomes ``[text block, *image blocks]``. Image tokens
ride along on every ReAct iteration (the trade recorded in the spec); the
once-per-turn alternative is ``vision_subagent``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from pydocs_mcp.ask_your_docs.architectures import register_architecture
from pydocs_mcp.ask_your_docs.architectures.base import (
    AgentArchitecture,
    AgentBuildContext,
    effective_tools,
)
from pydocs_mcp.ask_your_docs.prompts import prompts_for

# Back-compat alias — the text lives at prompts/inline/system_suffix_v1.j2,
# resolved by the architecture-name convention.
_IMAGE_ANALYSIS_PROMPT_SECTION = prompts_for("inline").render("system_suffix_v1")


@register_architecture("inline")
@dataclass(frozen=True, slots=True)
class InlineMultimodalArchitecture(AgentArchitecture):
    requires_multimodal: ClassVar[bool] = True

    def build(self, ctx: AgentBuildContext) -> Any:
        from langgraph.prebuilt import create_react_agent

        return create_react_agent(
            ctx.llm,
            effective_tools(ctx),
            prompt=ctx.prompt + self.prompts().render("system_suffix_v1"),
        )


__all__ = ("InlineMultimodalArchitecture",)
