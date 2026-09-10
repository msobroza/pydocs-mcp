"""Named fakes for agent-architecture tests (clean-code rule: no ad-hoc mocks).

FakeLlm is a minimal BaseChatModel: queued canned replies, records every
message list it is invoked with, and bind_tools returns self so
create_react_agent accepts it.

FakeMultiServerMCPClient stands in for langchain-mcp-adapters' client: it
records every instance's connection map (class-level ``recorded``, reset by
the fixture that installs it), spawns nothing, and returns no tools.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any, ClassVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
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
