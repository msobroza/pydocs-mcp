"""A ``:batch`` role's requests, run as one batch on OpenRouter's Batch API.

A ``:batch`` model is served only by the Batch API, which takes the base model
slug and resolves the ``:batch`` entry itself (OpenRouter's model-variants and
Batch API docs). The batch is submitted once — a repeated submit could run, and
bill, the whole batch twice — then polled, each poll retried like any idempotent
call, until it ends or the role's ``timeout_seconds`` runs out. A batch still
running at that deadline keeps running upstream: its rows fail, naming it, and
one log line records its id so it can be collected or deleted by hand.

Example:
    >>> batch_body("anthropic/claude-opus-5.5", [], effort=ReasoningEffort.HIGH)["endpoint"]
    '/v1/chat/completions'
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

import httpx

from pydocs_eval.judge.chat_wire import (
    ChatFailure,
    ChatOutcome,
    ChatRequest,
    completion_body,
    outcome_of,
)
from pydocs_eval.judge.openrouter_http import (
    CallPolicy,
    JudgeResponseError,
    JudgeUnavailableError,
    get_json,
    post_json,
)
from pydocs_eval.judge.role_config import ReasoningEffort
from pydocs_eval.judge.roles import ChatRole

log = logging.getLogger(__name__)

_BATCHES_ROUTE = "/batches"
_CHAT_COMPLETIONS_ENDPOINT = "/v1/chat/completions"
# Batches run for minutes; polling faster only spends requests.
_POLL_SECONDS = 15.0
# One submit or poll: a bound on a single HTTP call, not on the batch.
_CALL_TIMEOUT_SECONDS = 60.0
_OK = 200
# A call that got no usable answer: the rows fail, the run goes on.
_NO_ANSWER = (JudgeUnavailableError, JudgeResponseError)


class BatchStatus(StrEnum):
    """A batch's status as the Batch API reports it."""

    VALIDATING = "validating"
    IN_PROGRESS = "in_progress"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


_ENDED = frozenset(
    {BatchStatus.COMPLETED, BatchStatus.FAILED, BatchStatus.EXPIRED, BatchStatus.CANCELLED}
)


def base_slug(model: str) -> str:
    """``model`` without its variant: ``anthropic/claude-opus-5.5:batch`` → ``anthropic/claude-opus-5.5``."""
    return model.split(":", 1)[0]


def is_batch_model(model: str) -> bool:
    """Whether ``model`` names a ``:batch`` catalog variant, served only by the Batch API."""
    return "batch" in model.split(":")[1:]


def batch_body(
    model: str, requests: Sequence[ChatRequest], *, effort: ReasoningEffort
) -> dict[str, object]:
    """The batch submit body; ``endpoint`` and ``model`` precede ``requests``, as the API requires."""
    items = [
        {"custom_id": request.custom_id, "body": completion_body(request, effort)}
        for request in requests
    ]
    return {"endpoint": _CHAT_COMPLETIONS_ENDPOINT, "model": model, "requests": items}


@dataclass(frozen=True, slots=True)
class BatchRun:
    """One role's batch: submitted once, polled until it ends or the role's deadline passes."""

    role: ChatRole
    http: httpx.Client
    bearer: str
    sleep: Callable[[float], None]
    clock: Callable[[], float]

    def run(self, requests: Sequence[ChatRequest]) -> tuple[ChatOutcome, ...]:
        """Every row's outcome, in ``requests`` order."""
        try:
            batch_id = self._submit(requests)
        except _NO_ANSWER as exc:
            return _failed(requests, f"batch submit failed: {exc}")
        try:
            batch = self._wait(batch_id)
        except _NO_ANSWER as exc:
            return self._abandoned(batch_id, requests, f"could not be polled: {exc}")
        if batch is None:
            timeout = self.role.config.timeout_seconds
            return self._abandoned(batch_id, requests, f"still running after {timeout:g} s")
        return self._outcomes(batch_id, batch, requests)

    def _submit(self, requests: Sequence[ChatRequest]) -> str:
        body = batch_body(
            base_slug(self.role.model), requests, effort=self.role.config.reasoning_effort
        )
        submitted = post_json(
            self.http, self._url(), body, bearer=self.bearer, policy=self._policy(retries=0)
        )
        batch_id = submitted.get("id") if isinstance(submitted, Mapping) else None
        if not isinstance(batch_id, str) or not batch_id:
            raise JudgeUnavailableError(
                f"{self.role.model_key}: batch submit answered without an id"
            )
        return batch_id

    def _wait(self, batch_id: str) -> Mapping[str, object] | None:
        """The ended batch, or ``None`` once the role's deadline passes first."""
        deadline = self.clock() + self.role.config.timeout_seconds
        url = f"{self._url()}/{batch_id}"
        while True:
            batch = get_json(
                self.http, url, bearer=self.bearer, policy=self._policy(self.role.config.retries)
            )
            if isinstance(batch, Mapping) and batch.get("status") in _ENDED:
                return batch
            remaining = deadline - self.clock()
            if remaining <= 0:
                return None
            self.sleep(min(_POLL_SECONDS, remaining))

    def _outcomes(
        self, batch_id: str, batch: Mapping[str, object], requests: Sequence[ChatRequest]
    ) -> tuple[ChatOutcome, ...]:
        results = batch.get("results")
        by_id = {
            str(result.get("custom_id")): result
            for result in (results if isinstance(results, list) else [])
            if isinstance(result, Mapping)
        }
        ended = f"batch {batch_id} ended {batch.get('status')!s}: {_error_message(batch)}"
        return tuple(
            self._row(batch_id, request.custom_id, by_id.get(request.custom_id), ended)
            for request in requests
        )

    def _row(
        self, batch_id: str, custom_id: str, result: Mapping[str, object] | None, ended: str
    ) -> ChatOutcome:
        if result is None:
            return ChatFailure(custom_id, f"no result for this row: {ended}", batch_id)
        response = result.get("response")
        if not isinstance(response, Mapping) or response.get("status_code") != _OK:
            return ChatFailure(custom_id, f"row failed: {_error_message(result)}", batch_id)
        outcome = outcome_of(
            custom_id, response.get("body"), pinned=self.role.model, bearer=self.bearer
        )
        return replace(outcome, batch_id=batch_id) if isinstance(outcome, ChatFailure) else outcome

    def _abandoned(
        self, batch_id: str, requests: Sequence[ChatRequest], why: str
    ) -> tuple[ChatOutcome, ...]:
        """Every row failed while the batch may still run upstream; one log line keeps its id."""
        fields = {
            "event": "judge_batch_abandoned",
            "batch_id": batch_id,
            "role": self.role.model_key,
        }
        log.warning(json.dumps({**fields, "reason": why}))
        return _failed(requests, f"batch {batch_id} {why}; collect or delete it by id", batch_id)

    def _url(self) -> str:
        return f"{self.role.config.endpoint.rstrip('/')}{_BATCHES_ROUTE}"

    def _policy(self, retries: int) -> CallPolicy:
        timeout = min(_CALL_TIMEOUT_SECONDS, self.role.config.timeout_seconds)
        return CallPolicy(
            label=self.role.model_key, timeout_seconds=timeout, retries=retries, sleep=self.sleep
        )


def _failed(
    requests: Sequence[ChatRequest], reason: str, batch_id: str = ""
) -> tuple[ChatOutcome, ...]:
    return tuple(ChatFailure(request.custom_id, reason, batch_id) for request in requests)


def _error_message(holder: Mapping[str, object]) -> str:
    error = holder.get("error")
    message = error.get("message") if isinstance(error, Mapping) else error
    return str(message) if message else "no error given"


__all__ = ("BatchRun", "BatchStatus", "base_slug", "batch_body", "is_batch_model")
