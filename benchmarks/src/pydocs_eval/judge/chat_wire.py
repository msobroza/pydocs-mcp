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

from pydocs_eval.judge.openrouter_http import (
    ERROR_EXCERPT_CHARS,
    check_served_model,
    redact,
    usage_cost,
)
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
    """``request`` as a ``/v1/chat/completions`` body, without the model the caller adds.

    Example:
        >>> request = ChatRequest("q01", (ChatMessage(MessageRole.USER, "Label it."),), StructuredOutput("verdict", {}))
        >>> completion_body(request, ReasoningEffort.HIGH)["reasoning_effort"]
        'high'
    """
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


def chat_outcome_of(custom_id: str, body: object, *, pinned: str, bearer: str) -> ChatOutcome:
    """A chat-completion response body read as one row, its model checked against ``pinned``.

    A body or content that is not what was asked for fails the row, quoting it
    redacted.

    Example:
        >>> chat_outcome_of("q01", [], pinned="openai/gpt-6-luna", bearer="")
        ChatFailure(custom_id='q01', reason='response = [], expected a JSON object', batch_id='')

    Raises:
        JudgeModelMismatchError: another model served it.
    """
    if not isinstance(body, Mapping):
        return ChatFailure(custom_id, _unusable("response", body, bearer))
    served = str(body.get("model", ""))
    check_served_model(pinned, served, bearer=bearer)
    text = _first_message_text(body)
    content = _json_object(text)
    if content is None:
        return ChatFailure(custom_id, _unusable("choices[0].message.content", text, bearer))
    return ChatCompletion(custom_id, served, content, cost_usd=usage_cost(body))


def _first_message_text(body: Mapping[str, object]) -> object:
    """The first choice's message content as sent, or ``None`` when the body has none."""
    choices = body.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, Mapping) else None
    return message.get("content") if isinstance(message, Mapping) else None


def _json_object(text: object) -> Mapping[str, object] | None:
    """``text`` parsed as the JSON object a structured completion must be, else ``None``."""
    if not isinstance(text, str):
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _unusable(where: str, value: object, bearer: str) -> str:
    excerpt = redact(repr(value)[:ERROR_EXCERPT_CHARS], bearer)
    return f"{where} = {excerpt}, expected a JSON object"


__all__ = (
    "ChatCompletion",
    "ChatFailure",
    "ChatMessage",
    "ChatOutcome",
    "ChatRequest",
    "MessageRole",
    "StructuredOutput",
    "chat_outcome_of",
    "completion_body",
)
