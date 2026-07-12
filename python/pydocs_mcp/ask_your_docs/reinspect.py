"""``reinspect_images`` — re-contextualize earlier attachments to a NEW question.

An agent-LOCAL LangChain tool (NOT an MCP tool — the six-tool surface is
untouched): image bytes from recent turns live in a per-session store
(``attachments.update_image_store``), outside conversation history, and the
ReAct agent calls this tool when a new question refers back to one of them —
history shows ``[attached images: ...]`` placeholders it can pick names from.
One question-guided vision call over ONLY the selected images; the reply is
text facts in the same ERROR:/SYMBOL:/PATH: contract as the vision subagent.

Per-session isolation mirrors the scope pin: the store rides the
``agent._active_image_store`` contextvar set inside ``ask()``, never baked
into the (cross-session-cached) compiled graph.
"""

from __future__ import annotations

from typing import Any

_REINSPECT_DESCRIPTION = (
    "Re-read previously attached image(s) against the CURRENT question. Use "
    "when the user's new question refers to an image from an earlier turn — "
    "conversation history marks them as '[attached images: <names>]'. Pass "
    "ONLY the relevant names and the current question; returns fresh "
    "ERROR:/SYMBOL:/PATH:/TEXT:/VISUAL: fact lines extracted for it."
)


def build_reinspect_tool(llm: Any) -> Any:
    """Build the tool bound to ``llm`` (must be vision-capable — architectures
    only attach it when the detected capabilities say so)."""
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import StructuredTool

    from pydocs_mcp.ask_your_docs.agent import _active_image_store
    from pydocs_mcp.ask_your_docs.architectures.vision_subagent import (
        _VISION_EXTRACTION_PROMPT,
    )

    async def reinspect_images(names: list[str], question: str) -> str:
        store = _active_image_store.get() or {}
        if not store:
            return (
                "There are no previously attached images stored in this "
                "session — ask the user to re-attach the image."
            )
        missing = [n for n in names if n not in store]
        if missing:
            return (
                f"Unknown image name(s): {', '.join(missing)}. Stored images: {', '.join(store)}."
            )
        selected = [store[n] for n in names]
        reply = await llm.ainvoke(
            [
                HumanMessage(
                    content=[
                        {
                            "type": "text",
                            "text": _VISION_EXTRACTION_PROMPT.format(question=question),
                        },
                        *(att.as_content_block() for att in selected),
                    ]
                )
            ]
        )
        return str(reply.content).strip()

    return StructuredTool.from_function(
        coroutine=reinspect_images,
        name="reinspect_images",
        description=_REINSPECT_DESCRIPTION,
    )


__all__ = ("build_reinspect_tool",)
