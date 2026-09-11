"""Follow-up reformulation — a distinct consumer of the chat model (design §4.1, R1).

Moved verbatim out of ``agent.py`` (the line-budget extraction). Text-only by
contract: it runs on the woven question BEFORE image blocks are attached
(multimodal spec §3.6 decision 1), and history carries only text +
placeholders — ``_history_line`` enforces that shape defensively.
"""

from __future__ import annotations

from typing import Any

from pydocs_mcp.harness.ask_your_docs.chat_wire import NO_WIRE_PARAMS, WireParams
from pydocs_mcp.harness.ask_your_docs.prompts import rewrite_prompt


def _history_line(m) -> str:
    """One REWRITE_PROMPT history line — never a Python-list repr.

    History is text-by-construction (§3.6), but harden anyway: content-block
    messages flatten to their text parts plus "[image]" markers, so a
    multimodal message can never mangle the rewrite prompt.
    """
    content = m.content
    if isinstance(content, str):
        return f"{m.type}: {content}"
    parts = [
        b.get("text", "") if b.get("type") == "text" else "[image]"
        for b in content
        if isinstance(b, dict)
    ]
    return f"{m.type}: {' '.join(p for p in parts if p)}"


async def reformulate(
    llm: Any,
    history: list,
    question: str,
    *,
    rewrite_template: str | None = None,
    wire: WireParams = NO_WIRE_PARAMS,
) -> str:
    """Condense the last question + conversation into a standalone question.

    ``rewrite_template`` is the evaluation-harness override (a ``str.format``
    template with ``{history}`` / ``{question}``); ``None`` — the app's and
    CLI's only shape — renders the shipped ``rewrite_v1`` template. ``wire`` is
    what the main model was built with; see :func:`_rewrite_model` (P3).
    """
    if not history:
        return question
    lines = "\n".join(_history_line(m) for m in history)
    if rewrite_template is not None:
        prompt_text = rewrite_template.format(history=lines, question=question)
    else:
        prompt_text = rewrite_prompt(history=lines, question=question)
    reply = await _rewrite_model(llm, wire).ainvoke(prompt_text)
    return str(reply.content).strip() or question


def _rewrite_model(llm: Any, wire: WireParams) -> Any:
    """P3: a rewrite is deterministic, so a SENT temperature is pinned to 0 for this call.

    Keyed on the wire, never on ``llm.temperature`` alone: LangChain gives o1 a
    temperature of 1 by itself, and a no-params arm must stay byte-identical. A
    temperature LangChain dropped client-side (gpt-5 with Thinking on) stays dropped.
    """
    if "temperature" not in wire.sent_params or getattr(llm, "temperature", None) is None:
        return llm
    return llm.bind(temperature=0)


__all__ = ("reformulate",)
