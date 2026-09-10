"""``reinspect_images`` — re-contextualize earlier attachments to a NEW question.

An agent-LOCAL LangChain tool (NOT an MCP tool — the nine-tool surface is
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

# Module-level by design: bearer_tokens is light (httpx only, transitive via the required
# openai dep) and architectures/base.py already imports it this way; the langgraph/agent
# imports inside the builder stay function-local because THOSE are the heavy ones (AC-24).
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerSource,
    NoBearer,
    redact_bearer,
    translate_auth_errors,
)

_NO_BEARER = NoBearer()  # Null Object default: NoBearer is stateless, so one instance serves all


def build_reinspect_tool(llm: Any, *, max_per_turn: int, bearer: BearerSource = _NO_BEARER) -> Any:
    """Build the tool bound to ``llm`` (must be vision-capable — architectures
    only attach it when the detected capabilities say so). ``max_per_turn``
    comes from ``images.max_reinspect_per_turn`` at graph-build time;
    ``bearer`` is the connection's BearerSource for redacting a failure
    (the Null Object default has nothing to redact)."""
    from langchain_core.tools import StructuredTool

    from pydocs_mcp.harness.ask_your_docs.agent import _active_image_store, _reinspect_state
    from pydocs_mcp.harness.ask_your_docs.attachments import describe_images
    from pydocs_mcp.harness.ask_your_docs.prompts import BUDGET_MESSAGE, REINSPECT_DESCRIPTION

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
            return BUDGET_MESSAGE
        state["calls"] += 1
        selected = [store[n] for n in names]
        try:
            with translate_auth_errors(bearer):
                facts = await describe_images(
                    llm, question, [att.as_content_block() for att in selected]
                )
        except Exception as exc:  # broad on purpose: a tool RESULT, never a crash; redacted (H4)
            return f"Image re-inspection failed: {redact_bearer(str(exc), bearer)}"
        state["memo"][memo_key] = facts
        return facts

    return StructuredTool.from_function(
        coroutine=reinspect_images,
        name="reinspect_images",
        description=REINSPECT_DESCRIPTION,
    )


__all__ = ("build_reinspect_tool",)
