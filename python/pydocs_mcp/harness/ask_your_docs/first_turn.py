"""What the model's FIRST turn for one user question is shown.

Two things are assembled here, both about the same moment: the content of the
question message (its text, plus any attached image blocks), and — opt-in via
``ask_your_docs.seed_search_with_question`` — a ``search_codebase`` the harness
itself runs before the model gets to speak, handed to the model as a call that
already completed.

WHY seed at all: on ``repoqa-qa/small_test`` the user's question asked verbatim
retrieved 0.90 at k=10, against 0.73-0.77 for the queries the model wrote for
itself. The seed spends one call to put that retrieval in front of the model,
and shows it as a finished call so the model does not spend a turn repeating
the identical query.

langchain is imported function-locally, as everywhere else in this harness, so
importing this module costs no agent dependency — ``model_turns`` reads
:func:`is_seeded_search` and is deliberately langchain-free.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

#: The one tool a seeded first turn calls.
SEED_SEARCH_TOOL = "search_codebase"

#: Marks the assistant message the HARNESS wrote rather than the model. Read by
#: ``model_turns`` (to stamp the call turn 0) and by the eval binding (so the
#: seeded message is not counted as a model turn).
SEEDED_SEARCH_KEY = "pydocs_seeded_search"


class SeedSearchUnavailableError(ValueError):
    """The seed is on but no ``search_codebase`` tool is bound to the agent."""


def question_content(prefixed: str, images: Sequence[Any]) -> str | list:
    """The question message's content: plain text, or text plus image blocks."""
    if not images:
        return prefixed
    return [
        {"type": "text", "text": prefixed},
        *(attachment.as_content_block() for attachment in images),
    ]


def is_seeded_search(message: Any) -> bool:
    """Whether ``message`` is the assistant message a seeded search wrote."""
    marks = getattr(message, "additional_kwargs", None) or {}
    return bool(marks.get(SEEDED_SEARCH_KEY))


@dataclass(frozen=True, slots=True)
class SeededSearch:
    """Runs one ``search_codebase`` for a question and renders it as a done call."""

    tools: Sequence[Any]

    def _search_tool(self) -> Any:
        tool = next((t for t in self.tools if getattr(t, "name", "") == SEED_SEARCH_TOOL), None)
        if tool is None:
            raise SeedSearchUnavailableError(
                f"seed_search_with_question is on but no {SEED_SEARCH_TOOL!r} tool is bound;"
                f" bound tools are {sorted(getattr(t, 'name', '?') for t in self.tools)}"
            )
        return tool

    async def messages_for(self, question: str) -> list[Any]:
        """``[assistant tool call, tool result]`` for ``question``.

        The call goes through the agent's OWN bound tool, so it crosses the same
        MCP client, the same trace recorder and the same question-scope
        interceptor a call the model issued would — nothing about it is a
        shortcut around the server.

        WHY it carries only the query: the scope is NOT the caller's to apply.
        ``scope_interceptor`` owns that, reading the contextvars ``ask()`` bound
        before this runs, so the seed gets the question's pinned project /
        package / code filter and, over a multi-cell pin, the same fan-out and
        labeled merge — exactly what the model's own first search would get.
        """
        from langchain_core.messages import AIMessage

        call = {
            "name": SEED_SEARCH_TOOL,
            "args": {"query": question},
            "id": f"seed-{uuid4().hex[:12]}",
            "type": "tool_call",
        }
        result = await self._search_tool().ainvoke(call)
        proposal = AIMessage(
            content="",
            tool_calls=[call],
            additional_kwargs={SEEDED_SEARCH_KEY: True},
        )
        return [proposal, result]


def seeded_search_for(enabled: bool, tools: Sequence[Any]) -> SeededSearch | None:
    """The seeder a turn should use, or None when the knob is off."""
    return SeededSearch(tuple(tools)) if enabled else None


__all__ = (
    "SEEDED_SEARCH_KEY",
    "SEED_SEARCH_TOOL",
    "SeedSearchUnavailableError",
    "SeededSearch",
    "is_seeded_search",
    "question_content",
    "seeded_search_for",
)
