"""Builders for the langchain messages a chat turn produces — real message classes.

Shared by the behaviour-count and repro-record tests so both read one conversation.
Import after ``pytest.importorskip("langchain_core")``.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from pydocs_mcp.harness.ask_your_docs.first_turn import SEEDED_SEARCH_KEY

STRATEGIES = "src/needle/scoring/strategies.py"
MAXSIM = "needle.scoring.strategies.maxsim"
FINAL_ANSWER = "It is `maxsim` in strategies.py."


def call(call_id: str, tool: str, **args: object) -> dict:
    return {"id": call_id, "name": tool, "args": args, "type": "tool_call"}


def _usage(input_tokens: int, output_tokens: int) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def turn(*calls: dict, tokens: tuple[int, int] = (100, 10)) -> AIMessage:
    return AIMessage(content="", tool_calls=list(calls), usage_metadata=_usage(*tokens))


def result(call_id: str, tool: str, text: str, items: list | None = None) -> ToolMessage:
    structured = {
        "text": text,
        "items": items if items is not None else [{"path": "x"}],
        "meta": {},
    }
    return ToolMessage(
        content=[{"type": "text", "text": text}],
        tool_call_id=call_id,
        name=tool,
        artifact={"structured_content": structured},
    )


def answer(text: str = FINAL_ANSWER) -> AIMessage:
    return AIMessage(content=text, usage_metadata=_usage(500, 40))


def seeded_pair(query: str) -> list:
    proposal = AIMessage(
        content="",
        tool_calls=[call("seed-1", "search_codebase", query=query)],
        additional_kwargs={SEEDED_SEARCH_KEY: True},
    )
    return [proposal, result("seed-1", "search_codebase", "seeded hits")]


def conversation(question: str = "Where is the MaxSim score computed?") -> list:
    """Seeded search, then: an overview beside the first search; a card and its source view
    of one symbol; an unbounded read of the file the views showed; a zero-hit grep; an answer."""
    return [
        HumanMessage(content=question),
        *seeded_pair(question),
        turn(call("a", "get_overview"), call("b", "search_codebase", query="maxsim")),
        result("a", "get_overview", "needle: 3 packages"),
        result("b", "search_codebase", "maxsim in scoring"),
        turn(call("c", "get_symbol", target=MAXSIM)),
        result(
            "c", "get_symbol", f"{STRATEGIES}:10-30 def maxsim(...): [lines 1-9 not in the index]"
        ),
        turn(call("d", "get_symbol", target=MAXSIM, depth="source")),
        result("d", "get_symbol", f"{STRATEGIES}:10-30 source"),
        turn(call("e", "read_file", path=STRATEGIES), call("f", "grep", pattern="MaxSim")),
        result("e", "read_file", "whole file"),
        result("f", "grep", "no match", items=[]),
        answer(),
    ]
