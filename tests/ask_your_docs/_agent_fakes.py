"""Named fakes for agent-architecture tests (clean-code rule: no ad-hoc mocks).

FakeLlm is a minimal BaseChatModel: queued canned replies, records every
message list it is invoked with, and bind_tools returns self so
create_react_agent accepts it.
"""

from __future__ import annotations

from typing import Any

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
