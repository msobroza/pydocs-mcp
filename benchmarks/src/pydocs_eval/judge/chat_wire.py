"""The chat-completion wire format the three LLM roles share: a structured request, its outcome.

A request asks for JSON that follows a schema (OpenRouter's structured outputs,
``strict``) at the role's reasoning effort. Its outcome is the parsed JSON and
the model that served it, or a failure that names why — a row that failed is
the caller's to retry or report, never a row that silently reads empty. A
completion from any model other than the pin raises instead: nothing it wrote
may be used.

Example:
    >>> ChatRequest("q01", (ChatMessage(MessageRole.USER, "Label it."),), output).custom_id  # doctest: +SKIP
    'q01'
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydocs_eval.judge.openrouter_http import check_served_model
from pydocs_eval.judge.role_config import ReasoningEffort


class MessageRole(StrEnum):
    """Who a chat message speaks for."""

    SYSTEM = "system"
    USER = "user"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One message of a chat-completion request."""

    role: MessageRole
    content: str


@dataclass(frozen=True, slots=True)
class StructuredOutput:
    """The JSON schema a completion must follow, and the name the request gives it."""

    name: str
    schema: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """One completion a role asks for; ``custom_id`` names its row in the outcome."""

    custom_id: str
    messages: tuple[ChatMessage, ...]
    output: StructuredOutput


@dataclass(frozen=True, slots=True)
class ChatCompletion:
    """A row the pinned model answered: its structured content and what it cost."""

    custom_id: str
    served_model: str
    content: Mapping[str, object]
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class ChatFailure:
    """A row with no usable answer, and why; ``batch_id`` names the batch it ran in."""

    custom_id: str
    reason: str
    batch_id: str = ""


ChatOutcome = ChatCompletion | ChatFailure


def completion_body(request: ChatRequest, effort: ReasoningEffort) -> dict[str, object]:
    """``request`` as a ``/v1/chat/completions`` body, without the model the caller adds."""
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": request.output.name,
            "strict": True,
            "schema": request.output.schema,
        },
    }
    messages = [
        {"role": message.role.value, "content": message.content} for message in request.messages
    ]
    return {
        "messages": messages,
        "response_format": response_format,
        "reasoning_effort": effort.value,
    }


def outcome_of(custom_id: str, body: object, *, pinned: str, bearer: str) -> ChatOutcome:
    """A chat-completion response body read as a row, its model checked against ``pinned``.

    Raises:
        JudgeModelMismatchError: another model served it.
    """
    if not isinstance(body, Mapping):
        return ChatFailure(custom_id, f"response is {type(body).__name__}, expected a JSON object")
    served = str(body.get("model", ""))
    check_served_model(pinned, served, bearer=bearer)
    content = _content_of(body)
    if not isinstance(content, Mapping):
        return ChatFailure(custom_id, "completion content is not the requested JSON object")
    return ChatCompletion(custom_id, served, content, cost_usd=_cost_of(body))


def _content_of(body: Mapping[str, object]) -> object:
    """The first choice's message content, parsed as JSON; ``None`` when there is none."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return None
    message = choices[0].get("message")
    text = message.get("content") if isinstance(message, Mapping) else None
    try:
        return json.loads(text) if isinstance(text, str) else None
    except ValueError:
        return None


def _cost_of(body: Mapping[str, object]) -> float | None:
    usage = body.get("usage")
    cost = usage.get("cost") if isinstance(usage, Mapping) else None
    return float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None


__all__ = (
    "ChatCompletion",
    "ChatFailure",
    "ChatMessage",
    "ChatOutcome",
    "ChatRequest",
    "MessageRole",
    "StructuredOutput",
    "completion_body",
    "outcome_of",
)
