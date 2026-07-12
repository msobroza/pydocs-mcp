"""``vision_subagent`` — extraction node feeding a text-only agent (spec §3.4.2).

The repo's first hand-built StateGraph: a vision node runs ONE focused
multimodal call — guided by the user's (already-reformulated) question —
producing structured facts; the existing text ReAct agent (a compiled graph,
added as a node: LangGraph's pipeline-IS-a-step analogue) answers using those
facts woven into a text-only message. Image tokens are paid exactly once per
turn.
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


@register_architecture("vision_subagent")
@dataclass(frozen=True, slots=True)
class VisionSubagentArchitecture(AgentArchitecture):
    requires_multimodal: ClassVar[bool] = True

    def build(self, ctx: AgentBuildContext) -> Any:
        from langchain_core.messages import HumanMessage, RemoveMessage
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langgraph.prebuilt import create_react_agent

        react = create_react_agent(ctx.llm, effective_tools(ctx), prompt=ctx.prompt)

        async def vision_extract(state: MessagesState):
            last = state["messages"][-1]
            if isinstance(last.content, str):  # no image this turn
                return {}
            blocks = last.content
            question = next(b["text"] for b in blocks if b["type"] == "text")
            images = [b for b in blocks if b["type"] == "image_url"]
            reply = await ctx.llm.ainvoke(
                [
                    HumanMessage(
                        content=[
                            {
                                "type": "text",
                                "text": self.prompts().render(
                                    "vision_extraction_v1", question=question
                                ),
                            },
                            *images,
                        ]
                    )
                ]
            )
            facts = str(reply.content).strip()
            # Replace the multimodal message with a TEXT-ONLY message: facts
            # woven in the weave_attachments style, so the downstream ReAct
            # agent never sees image blocks.
            woven = (
                f"[image analysis]\n{facts}\n[/image analysis]\n{question}" if facts else question
            )
            # WHY RemoveMessage: MessagesState's ``add_messages`` reducer
            # merges by message id — a returned list APPENDS/updates, it
            # never deletes by omission. Without the explicit removal the
            # multimodal message would stay in state and the ReAct node
            # would still see (and re-pay for) the image blocks.
            return {"messages": [RemoveMessage(id=last.id), HumanMessage(woven)]}

        graph = StateGraph(MessagesState)
        graph.add_node("vision_extract", vision_extract)
        graph.add_node("react_agent", react)
        graph.add_edge(START, "vision_extract")
        graph.add_edge("vision_extract", "react_agent")
        graph.add_edge("react_agent", END)
        return graph.compile()


__all__ = ("VisionSubagentArchitecture",)
