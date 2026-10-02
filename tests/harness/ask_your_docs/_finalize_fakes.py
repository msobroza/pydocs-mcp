"""Named fakes for the finalize call's model (#375); ``_binding_fakes`` holds the
``FakeTurnFinalizer`` / ``fake_built_agent`` pair the turn-level tests use.

``FakeFinalizeLlm`` is the model a REAL ``TurnFinalizer`` talks to: it records every
``bind_tools`` keyword set and every request, and answers from a queue (each entry a
reply text, a ready ``AIMessage`` or an exception to raise).
``FakeLoopingFinalizeLlm`` is the looping tool model the prebuilt agent runs past its
budget, whose ``tool_choice="none"`` binding answers in prose — the forced-cap shape.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from ._agent_fakes import FakeReasoningToolLlm
from ._binding_fakes import FINALIZED_TEXT


class FakeFinalizeLlm(BaseChatModel):
    """Answers the finalize call from ``replies``; records its bindings and requests."""

    replies: list[Any] = Field(default_factory=list)
    bindings: list[dict[str, Any]] = Field(default_factory=list)
    requests: list[list[Any]] = Field(default_factory=list)
    # The body fields each request went out with (``parallel_tool_calls`` lives here).
    sent_model_kwargs: list[dict[str, Any]] = Field(default_factory=list)
    model_kwargs: dict[str, Any] = Field(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "fake-finalize-llm"

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeFinalizeLlm:
        self.bindings.append({"tools": list(tools), **kwargs})
        return self

    def _generate(self, messages: list, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        self.requests.append(list(messages))
        self.sent_model_kwargs.append(dict(self.model_kwargs))
        reply = self.replies.pop(0) if self.replies else FINALIZED_TEXT
        if isinstance(reply, BaseException):
            raise reply
        message = reply if isinstance(reply, AIMessage) else AIMessage(content=reply)
        return ChatResult(generations=[ChatGeneration(message=message)])


class FakeLoopingFinalizeLlm(FakeReasoningToolLlm):
    """Repeats its last tool-calling round forever; bound with ``tool_choice="none"`` it
    answers in prose, metered, like an endpoint honouring the finalize call."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        if kwargs.get("tool_choice") != "none":
            return self
        usage = {"input_tokens": 50, "output_tokens": 7, "total_tokens": 57}
        return FakeFinalizeLlm(replies=[AIMessage(content=FINALIZED_TEXT, usage_metadata=usage)])
