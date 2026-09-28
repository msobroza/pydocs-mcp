"""The one chat-completion client the three LLM roles share: escalation, alignment, references.

:class:`ChatCompleter` is the port a role's code depends on;
:class:`OpenRouterChatClient` the owned plain-HTTP adapter and
:class:`FakeOpenRouterChatClient` the offline double. A role pinned to a
``:batch`` model runs its requests as one batch (:mod:`openrouter_batch`); any
other model is asked one request at a time on ``/chat/completions``, each within
the role's timeout and retried only on a timeout or a 5xx. Either way every row
comes back — answered, or failed with its reason — in request order, and a row
answered by any model but the pin raises.

Example:
    >>> with httpx.Client() as http:  # doctest: +SKIP
    ...     client = OpenRouterChatClient(role=escalation_role(deployment.judge), http=http)
    ...     client.complete_all([request])
    (ChatCompletion(custom_id='q01', served_model='openai/gpt-6-luna', ...),)
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import httpx

from pydocs_eval.judge.chat_wire import (
    ChatCompletion,
    ChatFailure,
    ChatOutcome,
    ChatRequest,
    completion_body,
    outcome_of,
)
from pydocs_eval.judge.openrouter_batch import BatchRun, is_batch_model
from pydocs_eval.judge.openrouter_http import (
    CallPolicy,
    JudgeResponseError,
    JudgeUnavailableError,
    bearer_from_env,
    post_json,
)
from pydocs_eval.judge.roles import ChatRole

_CHAT_COMPLETIONS_ROUTE = "/chat/completions"
# Route only to endpoints that honour the structured output and the reasoning
# effort asked for, rather than to one that would silently drop either.
_PROVIDER_PREFERENCES = {"require_parameters": True}


@runtime_checkable
class ChatCompleter(Protocol):
    """Anything that answers a role's chat requests, one outcome per request, in order."""

    def complete_all(self, requests: Sequence[ChatRequest]) -> tuple[ChatOutcome, ...]: ...


@dataclass(frozen=True, slots=True)
class OpenRouterChatClient:
    """``role``'s pinned model on OpenRouter; ``sleep`` and ``clock`` pace retries and batch waits."""

    role: ChatRole
    http: httpx.Client
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    def complete_all(self, requests: Sequence[ChatRequest]) -> tuple[ChatOutcome, ...]:
        """Every request's outcome, in order.

        Raises:
            ValueError: two requests share a ``custom_id``, before any call.
            JudgeConfigError: the role's key variable is unset.
            JudgeRequestError: OpenRouter refused a request (a 4xx other than 429).
            JudgeModelMismatchError: a row was answered by another model.
        """
        _refuse_duplicate_ids(requests)
        bearer = bearer_from_env(self.role.config.api_key_env)
        if is_batch_model(self.role.model):
            return BatchRun(self.role, self.http, bearer, self.sleep, self.clock).run(requests)
        return tuple(self._complete(request, bearer) for request in requests)

    def _complete(self, request: ChatRequest, bearer: str) -> ChatOutcome:
        body = {
            "model": self.role.model,
            **completion_body(request, self.role.config.reasoning_effort),
            "provider": dict(_PROVIDER_PREFERENCES),
        }
        try:
            answered = post_json(self.http, self._url(), body, bearer=bearer, policy=self._policy())
        except (JudgeUnavailableError, JudgeResponseError) as exc:
            return ChatFailure(request.custom_id, str(exc))
        return outcome_of(request.custom_id, answered, pinned=self.role.model, bearer=bearer)

    def _url(self) -> str:
        return f"{self.role.config.endpoint.rstrip('/')}{_CHAT_COMPLETIONS_ROUTE}"

    def _policy(self) -> CallPolicy:
        return CallPolicy(
            label=self.role.model_key,
            timeout_seconds=self.role.config.timeout_seconds,
            retries=self.role.config.retries,
            sleep=self.sleep,
        )


def _refuse_duplicate_ids(requests: Sequence[ChatRequest]) -> None:
    repeated = sorted(
        key for key, count in Counter(r.custom_id for r in requests).items() if count > 1
    )
    if repeated:
        raise ValueError(f"custom_id {repeated!r} repeated, expected one request per row")


@dataclass(slots=True)
class FakeOpenRouterChatClient:
    """Scripted offline double: canned content by ``custom_id``, every request kept.

    An unscripted row fails, the real client's per-row failure path.
    """

    scripted: Mapping[str, Mapping[str, object]]
    served_model: str = "openai/gpt-6-luna"
    requests: list[ChatRequest] = field(default_factory=list, init=False)

    def complete_all(self, requests: Sequence[ChatRequest]) -> tuple[ChatOutcome, ...]:
        self.requests.extend(requests)
        return tuple(self._outcome(request) for request in requests)

    def _outcome(self, request: ChatRequest) -> ChatOutcome:
        content = self.scripted.get(request.custom_id)
        if content is None:
            return ChatFailure(request.custom_id, "fake chat client: row not scripted")
        return ChatCompletion(request.custom_id, self.served_model, content)


__all__ = ("ChatCompleter", "FakeOpenRouterChatClient", "OpenRouterChatClient")
