"""Follow-up reformulation — a distinct consumer of the chat model (design §4.1, R1).

Moved verbatim out of ``agent.py`` (the line-budget extraction). Text-only by
contract: it runs on the woven question BEFORE image blocks are attached
(multimodal spec §3.6 decision 1), and history carries only text +
placeholders — ``_history_line`` enforces that shape defensively.
"""

from __future__ import annotations

from typing import Any

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
) -> str:
    """Condense the last question + conversation into a standalone question.

    ``rewrite_template`` is the evaluation-harness override (a ``str.format``
    template with ``{history}`` / ``{question}``); ``None`` — the app's and
    CLI's only shape — renders the shipped ``rewrite_v1`` template.
    """
    if not history:
        return question
    lines = "\n".join(_history_line(m) for m in history)
    if rewrite_template is not None:
        prompt_text = rewrite_template.format(history=lines, question=question)
    else:
        prompt_text = rewrite_prompt(history=lines, question=question)
    reply = await llm.ainvoke(prompt_text)
    return str(reply.content).strip() or question


__all__ = ("reformulate",)
