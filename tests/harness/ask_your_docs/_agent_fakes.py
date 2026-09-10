"""Named fakes for agent-architecture tests (clean-code rule: no ad-hoc mocks).

FakeLlm is a minimal BaseChatModel: queued canned replies, records every
message list it is invoked with, and bind_tools returns self so
create_react_agent accepts it.

FakeMultiServerMCPClient stands in for langchain-mcp-adapters' client: it
records every instance's connection map (class-level ``recorded``, reset by
the fixture that installs it), spawns nothing, and returns no tools.

FakeReasoningToolLlm replays a scripted ReAct turn with the chunk shapes the
reasoning-capturing ChatOpenAI yields (reasoning deltas, split tool-call args,
usage on the last chunk); FakeActivityToolset builds MCP-shaped tools (text
blocks + a structured_content envelope, grep failing like isError=True).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import StructuredTool, ToolException
from pydantic import Field


class FakeLlm(BaseChatModel):
    """Records calls; replies from a queue (falling back to a fixed answer)."""

    replies: list[str] = Field(default_factory=list)
    calls: list[list[Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-llm"

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeLlm:
        return self

    def _generate(self, messages: list, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        self.calls.append(list(messages))
        text = self.replies.pop(0) if self.replies else "FAKE-ANSWER"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


class FakeVisionLlm(FakeLlm):
    """FakeLlm whose call log separates multimodal (vision) invocations."""

    @property
    def vision_calls(self) -> list[list[Any]]:
        return [
            msgs
            for msgs in self.calls
            if any(not isinstance(getattr(m, "content", ""), str) for m in msgs)
        ]


class FakeMcpSession:
    """Stands in for an MCP ClientSession; callers only hand it to ``load_mcp_tools``."""


class FakeMultiServerMCPClient:
    """Records each instance's ``connections``; ``get_tools`` / ``session`` never spawn."""

    recorded: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, connections: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.connections = connections or {}
        FakeMultiServerMCPClient.recorded.append(self.connections)

    async def get_tools(self) -> list[Any]:
        return []

    @contextlib.asynccontextmanager
    async def session(self, server_name: str) -> AsyncIterator[FakeMcpSession]:
        yield FakeMcpSession()


class FakeLoadMcpTools:
    """Stands in for ``load_mcp_tools``: records each session + interceptor list, binds no tools."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, list[Any]]] = []

    async def __call__(self, session: Any, *, tool_interceptors: list[Any] | None = None, **_: Any):
        self.calls.append((session, list(tool_interceptors or [])))
        return []


# One ReAct turn, as events_run.log recorded it: reasoning → three parallel calls (grep
# fails) → narration + one call → the answer. Round 2 has no reasoning, so its text is
# the narration a non-reasoning model writes before a tool call.
ACTIVITY_SCRIPT: list[dict[str, Any]] = [
    {
        "reasoning": "Search for routing, and fetch the overview and a grep in parallel.",
        "text": "",
        "tool_calls": [
            {"id": "call_search", "name": "search_codebase", "args": {"query": "routing"}},
            {"id": "call_over", "name": "get_overview", "args": {"package": "fastapi"}},
            {"id": "call_grep", "name": "grep", "args": {"pattern": "include_router("}},
        ],
    },
    {
        "reasoning": "",
        "text": "Let me open APIRouter. ",
        "tool_calls": [
            {
                "id": "call_sym",
                "name": "get_symbol",
                "args": {"target": "fastapi.routing.APIRouter"},
            }
        ],
    },
    {"reasoning": "", "text": "Routing is handled by APIRouter.", "tool_calls": []},
]


def _round_usage(turn: dict[str, Any]) -> dict[str, Any]:
    reasoning_tokens = 12 if turn["reasoning"] else 0
    details = {"reasoning": reasoning_tokens}
    return {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "output_token_details": details,
    }


def _round_chunks(turn: dict[str, Any]) -> list[AIMessageChunk]:
    half = len(turn["reasoning"]) // 2
    pieces = [turn["reasoning"][:half], turn["reasoning"][half:]] if turn["reasoning"] else []
    chunks = [
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": p}) for p in pieces
    ]
    chunks += [AIMessageChunk(content=turn["text"])] if turn["text"] else []
    for index, call in enumerate(turn["tool_calls"]):
        raw = json.dumps(call["args"])
        first = {"name": call["name"], "args": raw[:5], "id": call["id"], "index": index}
        rest = {"name": None, "args": raw[5:], "id": None, "index": index}
        chunks += [AIMessageChunk(content="", tool_call_chunks=[first])]
        chunks += [AIMessageChunk(content="", tool_call_chunks=[rest])]
    finish = {"finish_reason": "tool_calls" if turn["tool_calls"] else "stop", "model_name": "m"}
    last = AIMessageChunk(content="", response_metadata=finish, usage_metadata=_round_usage(turn))
    return [*chunks, last]


def _round_message(turn: dict[str, Any]) -> AIMessage:
    extra = {"reasoning_content": turn["reasoning"]} if turn["reasoning"] else {}
    calls = [{**call, "type": "tool_call"} for call in turn["tool_calls"]]
    return AIMessage(
        content=turn["text"],
        additional_kwargs=extra,
        tool_calls=calls,
        response_metadata={"model_name": "m"},
        usage_metadata=_round_usage(turn),
    )


class FakeReasoningToolLlm(BaseChatModel):
    """Replays ``script`` one round per call; streams when LangGraph's messages mode asks."""

    script: list[dict[str, Any]] = Field(default_factory=lambda: copy.deepcopy(ACTIVITY_SCRIPT))
    cursor: dict[str, int] = Field(default_factory=lambda: {"n": 0})

    @property
    def _llm_type(self) -> str:
        return "fake-reasoning-tool-llm"

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeReasoningToolLlm:
        return self

    def _next_round(self) -> dict[str, Any]:
        turn = self.script[min(self.cursor["n"], len(self.script) - 1)]
        self.cursor["n"] += 1
        return turn

    def _generate(self, messages: list, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=_round_message(self._next_round()))])

    async def _astream(
        self, messages: list, stop=None, run_manager=None, **kwargs: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in _round_chunks(self._next_round()):
            await asyncio.sleep(0)
            yield ChatGenerationChunk(message=chunk)


def _envelope(tool: str, text: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    meta = {"tool": tool, "project": "demo", "branch": "main", "truncated": False}
    return {
        "structured_content": {"text": text, "items": items, "meta": {**meta, "index_stale": False}}
    }


_SEARCH_ROWS = [
    {"kind": "chunk", "qualified_name": "fastapi.routing.APIRouter", "package": "fastapi",
     "path": "fastapi/routing.py", "start_line": 42, "end_line": 60, "score": 0.9},
    {"kind": "chunk", "qualified_name": "fastapi.FastAPI.include_router", "package": "fastapi",
     "path": "fastapi/applications.py", "start_line": 88, "end_line": 95, "score": 0.7},
]  # fmt: skip
_SYMBOL_ROWS = [
    {"node_id": "n1", "kind": "class", "qualified_name": "fastapi.routing.APIRouter",
     "path": "fastapi/routing.py", "start_line": 42, "end_line": 60},
]  # fmt: skip


class FakeActivityToolset:
    """MCP-shaped tools as langchain-mcp-adapters builds them: ``(text blocks, artifact)``.

    Completion order differs from call order on purpose (grep fails first, search last),
    so a consumer must pair results by ``tool_call_id``, never by position.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.tools = [
            self._tool("search_codebase", self._search),
            self._tool("get_overview", self._overview),
            self._tool("grep", self._grep),
            self._tool("get_symbol", self._symbol),
        ]

    @staticmethod
    def _tool(name: str, coroutine: Any) -> StructuredTool:
        return StructuredTool.from_function(
            coroutine=coroutine,
            name=name,
            description=f"fake {name}",
            response_format="content_and_artifact",
            handle_tool_error=True,
        )

    async def _search(self, query: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
        self.calls.append(("search_codebase", {"query": query}))
        await asyncio.sleep(0.05)
        text = f"2 hits for {query!r}"
        return [{"type": "text", "text": text}], _envelope("search_codebase", text, _SEARCH_ROWS)

    async def _overview(self, package: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
        self.calls.append(("get_overview", {"package": package}))
        await asyncio.sleep(0.02)
        text = f"# {package}\nModern web framework."
        return [{"type": "text", "text": text}], _envelope("get_overview", text, [])

    async def _grep(self, pattern: str) -> tuple[list[dict[str, str]], None]:
        self.calls.append(("grep", {"pattern": pattern}))
        raise ToolException(f"invalid regex: got {pattern!r}, unbalanced parenthesis")

    async def _symbol(self, target: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
        self.calls.append(("get_symbol", {"target": target}))
        text = f"class {target.rsplit('.', 1)[-1]}:"
        return [{"type": "text", "text": text}], _envelope("get_symbol", text, _SYMBOL_ROWS)
