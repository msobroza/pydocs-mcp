"""The one chat-completion client the three LLM roles share: escalation, alignment, references.

``chat_wire.ChatCompleter`` is the port a role's code depends on;
:class:`OpenRouterChatClient` is its owned plain-HTTP adapter and
:class:`FakeOpenRouterChatClient` the offline double. A role pinned to a
``:batch`` model runs its requests as one batch (:mod:`openrouter_batch`); any
other model is asked one request at a time on ``/chat/completions``, each within
the role's timeout and retried only on a timeout or a 5xx. Either way every row
comes back — answered, or failed with its reason — in request order, and a row
answered by any model but the pin raises.

A synchronous role asked at ``xhigh`` falls back to ``high`` when OpenRouter
refuses the effort (the spec: the first escalation call verifies that ``xhigh``
is accepted, and falls back to ``high``); the client then stays at ``high``.

Example:
    >>> with httpx.Client() as http:  # doctest: +SKIP
    ...     client = OpenRouterChatClient(role=escalation_role(deployment.judge), http=http)
    ...     client.complete_all([request])
    (ChatCompletion(custom_id='q01', served_model='openai/gpt-6-luna', ...),)
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import httpx

from pydocs_eval.judge.chat_wire import (
    BatchSubmitted,
    ChatCompletion,
    ChatFailure,
    ChatFailureKind,
    ChatOutcome,
    ChatRequest,
    chat_outcome_of,
    completion_body,
)
from pydocs_eval.judge.judge_errors import NO_USABLE_ANSWER_ERRORS, JudgeRequestError
from pydocs_eval.judge.model_ids import is_batch_model
from pydocs_eval.judge.openrouter_batch import BatchRun
from pydocs_eval.judge.openrouter_http import CallPolicy, bearer_from_env, post_json, route_url
from pydocs_eval.judge.role_config import ChatRole, ReasoningEffort

log = logging.getLogger(__name__)

_CHAT_COMPLETIONS_ROUTE = "/chat/completions"
_BAD_REQUEST = 400
# A 400 whose text names the effort refused the effort, not the request.
_EFFORT_WORDS = ("effort", "reasoning")


@dataclass(slots=True)
class OpenRouterChatClient:
    """``role``'s pinned model on OpenRouter; ``sleep`` and ``clock`` pace retries and batch waits."""

    role: ChatRole
    http: httpx.Client
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    _effort: ReasoningEffort = field(init=False)

    def __post_init__(self) -> None:
        self._effort = self.role.config.reasoning_effort

    def complete_all(
        self, requests: Sequence[ChatRequest], *, on_submitted: BatchSubmitted | None = None
    ) -> tuple[ChatOutcome, ...]:
        """Every request's outcome, in order; ``on_submitted`` hears a batch's id before any poll.

        Raises:
            ValueError: two requests share a ``custom_id``, before any call.
            JudgeConfigError: the role's key variable is unset.
            JudgeRequestError: OpenRouter refused a request (a 4xx other than 429).
            JudgeModelMismatchError: a row was answered by another model.
        """
        _refuse_duplicate_ids(requests)
        bearer = bearer_from_env(self.role.config.api_key_env)
        if is_batch_model(self.role.model):
            return self._batch_run(bearer).run(requests, on_submitted)
        return tuple(self._complete(request, bearer) for request in requests)

    def collect(self, batch_id: str, custom_ids: Sequence[str]) -> tuple[ChatOutcome, ...]:
        """The outcome of each of ``custom_ids`` in an already-submitted batch, in that order.

        Raises:
            ValueError: the role is not a ``:batch`` model, so it has no batch.
            JudgeModelMismatchError: a row was answered by another model.
        """
        return self._batch_run(self._batch_bearer()).collect(batch_id, custom_ids)

    def delete_batch(self, batch_id: str) -> None:
        """Delete an ended batch's stored inputs and results, once they are stored here.

        Raises:
            ValueError: the role is not a ``:batch`` model, so it has no batch.
            JudgeRequestError: OpenRouter refused (a 409 while the batch still runs).
        """
        self._batch_run(self._batch_bearer()).delete(batch_id)

    def _batch_bearer(self) -> str:
        if not is_batch_model(self.role.model):
            raise ValueError(
                f"{self.role.model_key} = {self.role.model!r} is not a ':batch' model, "
                "so it has no batch to collect or delete"
            )
        return bearer_from_env(self.role.config.api_key_env)

    def _batch_run(self, bearer: str) -> BatchRun:
        return BatchRun(self.role, self.http, bearer, self.sleep, self.clock)

    def _complete(self, request: ChatRequest, bearer: str) -> ChatOutcome:
        """One row, at ``high`` from here on when its endpoint refuses ``xhigh``."""
        try:
            return self._ask(request, bearer)
        except JudgeRequestError as exc:
            if not self._effort_refused(exc):
                raise
        _log_effort_fallback(self.role.model_key, self._effort)
        self._effort = ReasoningEffort.HIGH
        return self._ask(request, bearer)

    def _ask(self, request: ChatRequest, bearer: str) -> ChatOutcome:
        body = {"model": self.role.model, **completion_body(request, self._effort)}
        url = route_url(self.role.config, _CHAT_COMPLETIONS_ROUTE)
        policy = CallPolicy.of(self.role.model_key, self.role.config, self.sleep)
        try:
            answered = post_json(self.http, url, body, bearer=bearer, policy=policy)
        except NO_USABLE_ANSWER_ERRORS as exc:
            return ChatFailure(request.custom_id, str(exc))
        return chat_outcome_of(request.custom_id, answered, pinned=self.role.model, bearer=bearer)

    def _effort_refused(self, exc: JudgeRequestError) -> bool:
        """Whether ``exc`` refused ``xhigh`` itself, which ``high`` may answer instead."""
        said = exc.detail.lower()
        named = any(word in said for word in _EFFORT_WORDS)
        return self._effort is ReasoningEffort.XHIGH and exc.status_code == _BAD_REQUEST and named


def _log_effort_fallback(model_key: str, refused: ReasoningEffort) -> None:
    fields = {
        "event": "judge_effort_fallback",
        "role": model_key,
        "refused": refused.value,
        "now": ReasoningEffort.HIGH.value,
    }
    log.warning(json.dumps(fields))


def _refuse_duplicate_ids(requests: Sequence[ChatRequest]) -> None:
    counts = Counter(request.custom_id for request in requests)
    repeated = sorted(custom_id for custom_id, count in counts.items() if count > 1)
    if repeated:
        raise ValueError(f"custom_id {repeated!r} repeated, expected one request per row")


#: One scripted reply: the structured content the row answers with, or the kind of
#: failure it meets.
FakeChatReply = Mapping[str, object] | ChatFailureKind


@dataclass(slots=True)
class FakeOpenRouterChatClient:
    """Scripted offline double, batches included: every request, batch and deletion is kept.

    A row scripted with one content mapping answers it every time it is asked;
    a row scripted with a list gets its replies in order — each one content, or
    the kind of failure that ask meets — and fails once the list is spent. An
    unscripted row fails, the real client's per-row failure path. Every
    ``complete_all`` runs as one batch named ``<batch_prefix>_<n>``; a row left
    ``STILL_RUNNING`` in it is answered by its next reply when collected.

    Example:
        >>> FakeOpenRouterChatClient(scripted={}).complete_all([])
        ()
    """

    scripted: Mapping[str, FakeChatReply | Sequence[FakeChatReply]]
    served_model: str = "openai/gpt-6-luna"
    batch_prefix: str = "fake_batch"
    requests: list[ChatRequest] = field(default_factory=list, init=False)
    batches: list[tuple[str, tuple[str, ...]]] = field(default_factory=list, init=False)
    deleted: list[str] = field(default_factory=list, init=False)
    _asked: Counter[str] = field(default_factory=Counter, init=False)

    def complete_all(
        self, requests: Sequence[ChatRequest], *, on_submitted: BatchSubmitted | None = None
    ) -> tuple[ChatOutcome, ...]:
        self.requests.extend(requests)
        batch_id = f"{self.batch_prefix}_{len(self.batches) + 1}"
        self.batches.append((batch_id, tuple(request.custom_id for request in requests)))
        if on_submitted is not None:
            on_submitted(batch_id)
        return self.collect(batch_id, [request.custom_id for request in requests])

    def collect(self, batch_id: str, custom_ids: Sequence[str]) -> tuple[ChatOutcome, ...]:
        return tuple(self._outcome(batch_id, custom_id) for custom_id in custom_ids)

    def delete_batch(self, batch_id: str) -> None:
        self.deleted.append(batch_id)

    def _outcome(self, batch_id: str, custom_id: str) -> ChatOutcome:
        reply = self._next_reply(custom_id)
        if reply is None:
            return ChatFailure(custom_id, "fake chat client: row not scripted", batch_id)
        if isinstance(reply, ChatFailureKind):
            return ChatFailure(custom_id, f"fake chat client: {reply.value}", batch_id, reply)
        return ChatCompletion(custom_id, self.served_model, reply, batch_id=batch_id)

    def _next_reply(self, custom_id: str) -> FakeChatReply | None:
        script = self.scripted.get(custom_id)
        if script is None or isinstance(script, Mapping | ChatFailureKind):
            return script
        asked = self._asked[custom_id]
        self._asked[custom_id] += 1
        return script[asked] if asked < len(script) else None


__all__ = ("FakeChatReply", "FakeOpenRouterChatClient", "OpenRouterChatClient")
