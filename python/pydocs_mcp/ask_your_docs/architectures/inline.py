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

from pydocs_mcp.ask_your_docs.architectures import agent_registry
from pydocs_mcp.ask_your_docs.architectures.base import (
    AgentArchitecture,
    AgentBuildContext,
    effective_tools,
)

_IMAGE_ANALYSIS_PROMPT_SECTION = """
Image handling:
- The user may attach screenshots or diagrams. FIRST extract what is relevant
  to the question (error messages verbatim, symbol names, file paths, axis
  labels, box/arrow labels), THEN use your tools to ground every extracted
  name in the indexed corpus before citing it.
- Never answer from the image alone when a tool can verify; never invent
  symbols the image does not show.
"""


@agent_registry.register("inline")
@dataclass(frozen=True, slots=True)
class InlineMultimodalArchitecture(AgentArchitecture):
    requires_multimodal: ClassVar[bool] = True

    def build(self, ctx: AgentBuildContext) -> Any:
        from langgraph.prebuilt import create_react_agent

        return create_react_agent(
            ctx.llm,
            effective_tools(ctx),
            prompt=ctx.prompt + _IMAGE_ANALYSIS_PROMPT_SECTION,
        )


__all__ = ("InlineMultimodalArchitecture",)
