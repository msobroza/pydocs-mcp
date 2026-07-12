"""``reinspect_images`` — re-contextualize earlier attachments to a NEW question.

An agent-LOCAL LangChain tool (NOT an MCP tool — the six-tool surface is
untouched): image bytes from recent turns live in a per-session store
(``attachments.update_image_store``), outside conversation history, and the
ReAct agent calls this tool when a new question refers back to one of them —
history shows ``[attached images: ...]`` placeholders it can pick names from.
One question-guided vision call over ONLY the selected images; the reply is
text facts in the same ERROR:/SYMBOL:/PATH: contract as the vision subagent.

Necessity gating (each call is a full vision-model call):
- repeated same-args calls within a turn return the memoized facts (free);
- a per-turn budget (``images.max_reinspect_per_turn``) hard-stops a looping
  agent — beyond it the tool refuses and tells the model to answer from the
  facts it already has;
- the store snapshot a turn receives contains only PRIOR turns' images, so
  the current attachment (just seen/extracted) is never redundantly re-read.

Per-session/turn isolation mirrors the scope pin: the store and the budget
state ride ``agent._active_image_store`` / ``agent._reinspect_state``
contextvars set inside ``ask()``, never baked into the (cross-session-cached)
compiled graph.
"""

from __future__ import annotations

from typing import Any

_REINSPECT_DESCRIPTION = (
    "Re-read previously attached image(s) against the CURRENT question. "
    "EXPENSIVE: every call costs a full vision-model call and a per-turn "
    "budget applies — call it ONLY when the answer genuinely depends on an "
    "earlier image's content that is not already in the conversation "
    "(existing [image analysis] facts stay valid — reuse them instead of "
    "re-reading). History marks earlier attachments as "
    "'[attached images: <names>]'. Pass ONLY the relevant names and the "
    "current question; returns fresh ERROR:/SYMBOL:/PATH:/TEXT:/VISUAL: "
    "fact lines."
)

_BUDGET_MESSAGE = (
    "reinspect budget for this turn is exhausted — answer from the image "
    "facts you already have in the conversation, or ask the user to "
    "re-attach the image if something essential is still missing."
)


def build_reinspect_tool(llm: Any, *, max_per_turn: int) -> Any:
    """Build the tool bound to ``llm`` (must be vision-capable — architectures
    only attach it when the detected capabilities say so). ``max_per_turn``
    comes from ``images.max_reinspect_per_turn`` at graph-build time."""
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import StructuredTool

    from pydocs_mcp.ask_your_docs.agent import _active_image_store, _reinspect_state
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
        if not names:
            return (
                "No image names given — pass the relevant names from the "
                f"'[attached images: ...]' history markers. Stored images: "
                f"{', '.join(store)}."
            )
        missing = [n for n in names if n not in store]
        if missing:
            return (
                f"Unknown image name(s): {', '.join(missing)}. Stored images: {', '.join(store)}."
            )
        state = _reinspect_state.get() or {"calls": 0, "memo": {}}
        memo_key = (tuple(sorted(names)), question)
        if memo_key in state["memo"]:  # repeat call — free
            return state["memo"][memo_key]
        if state["calls"] >= max_per_turn:
            return _BUDGET_MESSAGE
        state["calls"] += 1
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
        facts = str(reply.content).strip()
        state["memo"][memo_key] = facts
        return facts

    return StructuredTool.from_function(
        coroutine=reinspect_images,
        name="reinspect_images",
        description=_REINSPECT_DESCRIPTION,
    )


__all__ = ("build_reinspect_tool",)
