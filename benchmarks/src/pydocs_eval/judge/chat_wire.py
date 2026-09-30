"""The chat-completion wire format the three LLM roles share: a structured request, its outcome.

A request asks for JSON that follows a schema (OpenRouter's structured outputs,
``strict``) at the role's reasoning effort. Its outcome is the parsed JSON and
the model that served it, or a failure that names why and of which kind — a row
that failed is the caller's to retry or report, never a row that silently reads
empty. A completion from any model other than the pin raises instead: nothing
it wrote may be used.

Example:
    >>> ChatRequest("q01", (ChatMessage(MessageRole.USER, "Label it."),), output).custom_id  # doctest: +SKIP
    'q01'
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydocs_eval.judge.model_ids import check_served_model
from pydocs_eval.judge.openrouter_body import redacted_excerpt, usage_cost
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
    """A row the pinned model answered: its structured content and what it cost.

    ``batch_id`` names the batch that answered it, empty for a synchronous call:
    a batch whose results are stored is deleted by that id.
    """

    custom_id: str
    served_model: str
    content: Mapping[str, object]
    cost_usd: float | None = None
    batch_id: str = ""


class ChatFailureKind(StrEnum):
    """Why a row has no usable answer — which decides what its caller may do next.

    Only a ``JOB_FAILED`` row may be asked of a fallback model. A
    ``STILL_RUNNING`` row may yet be answered upstream, and billed: it is
    collected later by its batch id, never asked again. A ``NOT_SUBMITTED`` row
    is never asked again automatically either, since a submit that failed on a
    timeout may have been accepted after all.
    """

    #: The service answered the row with an error, or its batch ended
    #: (``failed``, ``expired``, ``cancelled``) without answering it.
    JOB_FAILED = "job_failed"
    #: The model answered, but not with the JSON asked for.
    UNUSABLE_ANSWER = "unusable_answer"
    #: Given up on while its batch may still run upstream.
    STILL_RUNNING = "still_running"
    #: The batch submit failed: the row may never have run.
    NOT_SUBMITTED = "not_submitted"


@dataclass(frozen=True, slots=True)
class ChatFailure:
    """A row with no usable answer, why, and what kind of failure that is.

    ``batch_id`` names the batch it ran in, empty when there was none.
    """

    custom_id: str
    reason: str
    batch_id: str = ""
    kind: ChatFailureKind = ChatFailureKind.JOB_FAILED


ChatOutcome = ChatCompletion | ChatFailure


#: Told a batch's id as soon as it is submitted, before any poll: the one moment
#: a caller can record it before a crash or a deadline could lose it.
BatchSubmitted = Callable[[str], None]


@runtime_checkable
class ChatCompleter(Protocol):
    """Anything that answers a role's chat requests, one outcome per request, in order.

    ``on_submitted`` is told the id of every batch the requests run in.
    """

    def complete_all(
        self, requests: Sequence[ChatRequest], *, on_submitted: BatchSubmitted | None = None
    ) -> tuple[ChatOutcome, ...]: ...


@runtime_checkable
class BatchChatCompleter(ChatCompleter, Protocol):
    """A completer whose batches outlive one call: collected later by id, deleted once stored."""

    def collect(self, batch_id: str, custom_ids: Sequence[str]) -> tuple[ChatOutcome, ...]: ...

    def delete_batch(self, batch_id: str) -> None: ...


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

    A body or content that is not what was asked for fails the row as an
    unusable answer, quoting it redacted.

    Example:
        >>> failure = chat_outcome_of("q01", [], pinned="openai/gpt-6-luna", bearer="")
        >>> failure.reason, failure.kind.value
        ('response = [], expected a JSON object', 'unusable_answer')

    Raises:
        JudgeModelMismatchError: another model served it.
    """
    if not isinstance(body, Mapping):
        return _unusable_failure(custom_id, "response", body, bearer)
    served = str(body.get("model", ""))
    check_served_model(pinned, served, bearer=bearer)
    text = _first_message_text(body)
    content = _json_object(text)
    if content is None:
        return _unusable_failure(custom_id, "choices[0].message.content", text, bearer)
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


def _unusable_failure(custom_id: str, where: str, value: object, bearer: str) -> ChatFailure:
    excerpt = redacted_excerpt(repr(value), bearer)
    reason = f"{where} = {excerpt}, expected a JSON object"
    return ChatFailure(custom_id, reason, kind=ChatFailureKind.UNUSABLE_ANSWER)


__all__ = (
    "BatchChatCompleter",
    "BatchSubmitted",
    "ChatCompleter",
    "ChatCompletion",
    "ChatFailure",
    "ChatFailureKind",
    "ChatMessage",
    "ChatOutcome",
    "ChatRequest",
    "MessageRole",
    "StructuredOutput",
    "chat_outcome_of",
    "completion_body",
)
