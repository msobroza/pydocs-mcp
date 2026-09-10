"""``vision_subagent`` — extraction node feeding a text-only agent (spec §3.4.2).

The repo's first hand-built StateGraph: a vision node runs ONE focused
multimodal call — guided by the user's (already-reformulated) question —
producing structured facts; the existing text ReAct agent (a compiled graph,
added as a node: LangGraph's pipeline-IS-a-step analogue) answers using those
facts woven into a text-only message. Image tokens are paid exactly once per
turn. The image call goes to ``ctx.vision_llm`` (design §4.8), which is
``ctx.llm`` unless a separate vision model is configured.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from pydocs_mcp.harness.ask_your_docs.architectures import register_architecture
from pydocs_mcp.harness.ask_your_docs.architectures.base import (
    AgentArchitecture,
    AgentBuildContext,
    ImageModelRoute,
    effective_tools,
)


def _vision_extract_node(ctx: AgentBuildContext, render: Callable[..., str]) -> Any:
    """The extraction node: ONE image call on ``ctx.vision_llm``, replacing the
    multimodal message with a text-only one carrying the extracted facts."""
    from langchain_core.messages import HumanMessage, RemoveMessage
    from langgraph.graph import MessagesState

    from pydocs_mcp.harness.ask_your_docs.attachments import describe_images
    from pydocs_mcp.harness.ask_your_docs.bearer_tokens import translate_auth_errors

    # Bound OUT of the context: the compiled graph is cached for the process lifetime, so a
    # closure over ctx would keep the tools, the prompt and the config records alive with it.
    vision_llm, bearer = ctx.vision_llm, ctx.bearer

    async def vision_extract(state: MessagesState) -> dict:
        last = state["messages"][-1]
        if isinstance(last.content, str):  # no image this turn
            return {}
        blocks = last.content
        question = next(b["text"] for b in blocks if b["type"] == "text")
        images = [b for b in blocks if b["type"] == "image_url"]
        # The person attached the image on purpose: a failure propagates to
        # the app's send-loop boundary, which renders it redacted (§4.8).
        with translate_auth_errors(bearer):
            facts = await describe_images(vision_llm, question, images, render=render)
        # WHY RemoveMessage: MessagesState's ``add_messages`` reducer merges by
        # message id — a returned list APPENDS/updates, it never deletes by
        # omission. Without the explicit removal the multimodal message would
        # stay in state and the ReAct node would still see (and re-pay for)
        # the image blocks.
        return {"messages": [RemoveMessage(id=last.id), HumanMessage(_woven(facts, question))]}

    return vision_extract


def _woven(facts: str, question: str) -> str:
    """The facts woven into the question in the weave_attachments style, so the
    downstream ReAct agent never sees image blocks."""
    return f"[image analysis]\n{facts}\n[/image analysis]\n{question}" if facts else question


@register_architecture("vision_subagent")
@dataclass(frozen=True, slots=True)
class VisionSubagentArchitecture(AgentArchitecture):
    requires_multimodal: ClassVar[bool] = True
    image_model_route: ClassVar[ImageModelRoute] = ImageModelRoute.VISION

    def build(self, ctx: AgentBuildContext) -> Any:
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langgraph.prebuilt import create_react_agent

        react = create_react_agent(
            ctx.llm, effective_tools(ctx, self.image_model_route), prompt=ctx.prompt
        )
        graph = StateGraph(MessagesState)
        graph.add_node("vision_extract", _vision_extract_node(ctx, self.prompts().render))
        graph.add_node("react_agent", react)
        graph.add_edge(START, "vision_extract")
        graph.add_edge("vision_extract", "react_agent")
        graph.add_edge("react_agent", END)
        return graph.compile()


__all__ = ("VisionSubagentArchitecture",)
