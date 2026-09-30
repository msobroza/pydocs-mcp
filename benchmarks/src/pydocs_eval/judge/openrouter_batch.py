"""A ``:batch`` role's requests, run as one batch on OpenRouter's Batch API.

A ``:batch`` model is served only by the Batch API, which takes the base model
slug and resolves the ``:batch`` entry itself (OpenRouter's model-variants and
Batch API docs). The batch is submitted once — a repeated submit could run, and
bill, the whole batch twice — and its id is logged and handed to the caller
before the first poll. It is then polled, each poll retried like any idempotent
call, until it ends or the role's ``timeout_seconds`` runs out.

The Batch API has a 24 h window and no cancel endpoint, so a batch given up on
may still be answered, and billed, upstream: its rows fail as
``STILL_RUNNING``, one log line records its id, and :meth:`BatchRun.collect`
reads it later by that id. A batch that ended is deleted by id once its results
are stored (:meth:`BatchRun.delete`); OpenRouter otherwise keeps its inputs and
results for 30 days.

Example:
    >>> batch_body("anthropic/claude-opus-5.5", [], effort=ReasoningEffort.HIGH)["endpoint"]
    '/v1/chat/completions'
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

import httpx

from pydocs_eval.judge.chat_wire import (
    BatchSubmitted,
    ChatFailure,
    ChatFailureKind,
    ChatOutcome,
    ChatRequest,
    chat_outcome_of,
    completion_body,
)
from pydocs_eval.judge.judge_errors import (
    NO_USABLE_ANSWER_ERRORS,
    JudgeRequestError,
    JudgeResponseError,
)
from pydocs_eval.judge.model_ids import base_slug
from pydocs_eval.judge.openrouter_body import redacted_excerpt
from pydocs_eval.judge.openrouter_http import (
    CallPolicy,
    delete_url,
    get_json,
    post_json,
    route_url,
)
from pydocs_eval.judge.role_config import ChatRole, ReasoningEffort

log = logging.getLogger(__name__)

_BATCHES_ROUTE = "/batches"
_CHAT_COMPLETIONS_ENDPOINT = "/v1/chat/completions"
# Batches run for minutes; polling faster only spends requests.
_POLL_SECONDS = 15.0
# One submit or poll: a bound on a single HTTP call, not on the batch.
_CALL_TIMEOUT_SECONDS = 60.0
_OK = 200
_NOT_FOUND = 404
# The row ids every batch backend accepts (Anthropic's is the strictest), and how
# any other id is shortened: a fixed prefix and 32 hex digits of its sha256.
_WIRE_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_WIRE_ID_PREFIX = "row-"
_WIRE_DIGEST_CHARS = 32


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


def wire_custom_id(custom_id: str) -> str:
    """The id a row goes by on the Batch API: itself when the backends accept it, else its hash.

    Anthropic's batch backend takes only ``[A-Za-z0-9_-]{1,64}`` (the first paid
    pilot, 2026-09-30, was refused with "this provider caps custom_id at 64
    characters"), so any other id is sent as a stable 128-bit digest and read
    back under the caller's own id.

    Example:
        >>> wire_custom_id("q01"), len(wire_custom_id("repoqa-qa/repo_qa/a/b@1234567/c.py::d"))
        ('q01', 36)
    """
    if _WIRE_SAFE_ID.fullmatch(custom_id):
        return custom_id
    digest = hashlib.sha256(custom_id.encode("utf-8")).hexdigest()[:_WIRE_DIGEST_CHARS]
    return f"{_WIRE_ID_PREFIX}{digest}"


def batch_body(
    model: str, requests: Sequence[ChatRequest], *, effort: ReasoningEffort
) -> dict[str, object]:
    """The batch submit body; ``endpoint`` and ``model`` precede ``requests``, as the API requires.

    Each row goes by its :func:`wire_custom_id`.

    Example:
        >>> list(batch_body("openai/gpt-6-astra", [], effort=ReasoningEffort.HIGH))
        ['endpoint', 'model', 'requests']

    Raises:
        ValueError: two rows would go by one wire id.
    """
    wire_ids = [wire_custom_id(request.custom_id) for request in requests]
    if len(set(wire_ids)) != len(wire_ids):
        raise ValueError(f"custom ids {wire_ids!r} collide on the wire, expected distinct ids")
    items = [
        {"custom_id": wire_id, "body": completion_body(request, effort)}
        for wire_id, request in zip(wire_ids, requests, strict=True)
    ]
    return {"endpoint": _CHAT_COMPLETIONS_ENDPOINT, "model": model, "requests": items}


@dataclass(frozen=True, slots=True)
class BatchRun:
    """One role's batches: submitted once, polled until each ends or the role's deadline passes."""

    role: ChatRole
    http: httpx.Client
    bearer: str
    sleep: Callable[[float], None]
    clock: Callable[[], float]

    def run(
        self, requests: Sequence[ChatRequest], on_submitted: BatchSubmitted | None = None
    ) -> tuple[ChatOutcome, ...]:
        """Every row's outcome, in ``requests`` order; ``on_submitted`` hears the batch id first."""
        custom_ids = [request.custom_id for request in requests]
        try:
            batch_id = self._submit(requests)
        except NO_USABLE_ANSWER_ERRORS as exc:
            reason = f"batch submit failed: {exc}"
            return _failed(custom_ids, reason, kind=ChatFailureKind.NOT_SUBMITTED)
        self._log(logging.INFO, "judge_batch_submitted", batch_id, rows=len(custom_ids))
        if on_submitted is not None:
            on_submitted(batch_id)
        return self.collect(batch_id, custom_ids)

    def collect(self, batch_id: str, custom_ids: Sequence[str]) -> tuple[ChatOutcome, ...]:
        """The outcome of each of ``custom_ids`` in batch ``batch_id``, waited on within the deadline."""
        try:
            batch = self._wait(batch_id)
        except NO_USABLE_ANSWER_ERRORS as exc:
            return self._abandoned(batch_id, custom_ids, f"could not be polled: {exc}")
        if batch is None:
            timeout = self.role.config.timeout_seconds
            return self._abandoned(batch_id, custom_ids, f"still running after {timeout:g} s")
        return self._outcomes(batch_id, batch, custom_ids)

    def delete(self, batch_id: str) -> None:
        """Delete an ended batch's stored inputs and results; one already gone counts as deleted.

        Raises:
            JudgeRequestError: OpenRouter refused (a 409 while the batch still runs).
            JudgeUnavailableError: no answer after every retry.
        """
        try:
            delete_url(
                self.http, self._batch_url(batch_id), bearer=self.bearer, policy=self._call_policy()
            )
        except JudgeRequestError as exc:
            if exc.status_code != _NOT_FOUND:
                raise
        self._log(logging.INFO, "judge_batch_deleted", batch_id)

    def _submit(self, requests: Sequence[ChatRequest]) -> str:
        config = self.role.config
        body = batch_body(base_slug(config.model), requests, effort=config.reasoning_effort)
        policy = replace(self._call_policy(), retries=0)
        submitted = post_json(self.http, self._url(), body, bearer=self.bearer, policy=policy)
        batch_id = submitted.get("id") if isinstance(submitted, Mapping) else None
        if not isinstance(batch_id, str) or not batch_id:
            excerpt = redacted_excerpt(repr(submitted), self.bearer)
            raise JudgeResponseError(f"{self.role.model_key}: batch submit answered {excerpt}")
        return batch_id

    def _wait(self, batch_id: str) -> Mapping[str, object] | None:
        """The ended batch, or ``None`` once the role's deadline passes first."""
        deadline = self.clock() + self.role.config.timeout_seconds
        url = self._batch_url(batch_id)
        policy = self._call_policy()
        while True:
            batch = get_json(self.http, url, bearer=self.bearer, policy=policy)
            if isinstance(batch, Mapping) and batch.get("status") in _ENDED:
                return batch
            remaining = deadline - self.clock()
            if remaining <= 0:
                return None
            self.sleep(min(_POLL_SECONDS, remaining))

    def _outcomes(
        self, batch_id: str, batch: Mapping[str, object], custom_ids: Sequence[str]
    ) -> tuple[ChatOutcome, ...]:
        results = batch.get("results")
        listed = results if isinstance(results, list) else []
        by_id = {
            str(result.get("custom_id")): result for result in listed if isinstance(result, Mapping)
        }
        ended = f"batch {batch_id} ended {batch.get('status')}: {self._error_of(batch)}"
        return tuple(
            self._row_outcome(batch_id, custom_id, by_id.get(wire_custom_id(custom_id)), ended)
            for custom_id in custom_ids
        )

    def _row_outcome(
        self, batch_id: str, custom_id: str, result: Mapping[str, object] | None, ended: str
    ) -> ChatOutcome:
        if result is None:
            return ChatFailure(custom_id, f"no result for this row: {ended}", batch_id)
        response = result.get("response")
        if not isinstance(response, Mapping) or response.get("status_code") != _OK:
            return ChatFailure(custom_id, f"row failed: {self._error_of(result)}", batch_id)
        outcome = chat_outcome_of(
            custom_id, response.get("body"), pinned=self.role.model, bearer=self.bearer
        )
        return replace(outcome, batch_id=batch_id)

    def _abandoned(
        self, batch_id: str, custom_ids: Sequence[str], why: str
    ) -> tuple[ChatOutcome, ...]:
        """Every row given up on while the batch may still run upstream; one log line keeps its id."""
        self._log(logging.WARNING, "judge_batch_abandoned", batch_id, reason=why)
        reason = f"batch {batch_id} {why}; collect it by id"
        return _failed(custom_ids, reason, batch_id, kind=ChatFailureKind.STILL_RUNNING)

    def _log(self, level: int, event: str, batch_id: str, **details: object) -> None:
        fields = {"event": event, "batch_id": batch_id, "role": self.role.model_key, **details}
        log.log(level, json.dumps(fields))

    def _error_of(self, holder: Mapping[str, object]) -> str:
        """The error message ``holder`` carries, redacted: the Batch API's words, not ours."""
        error = holder.get("error")
        message = error.get("message") if isinstance(error, Mapping) else error
        return redacted_excerpt(str(message), self.bearer) if message else "no error given"

    def _url(self) -> str:
        return route_url(self.role.config, _BATCHES_ROUTE)

    def _batch_url(self, batch_id: str) -> str:
        return f"{self._url()}/{batch_id}"

    def _call_policy(self) -> CallPolicy:
        """The role's retries, within a bound on one call rather than on the whole batch."""
        policy = CallPolicy.of(self.role.model_key, self.role.config, self.sleep)
        return replace(policy, timeout_seconds=min(_CALL_TIMEOUT_SECONDS, policy.timeout_seconds))


def _failed(
    custom_ids: Sequence[str], reason: str, batch_id: str = "", *, kind: ChatFailureKind
) -> tuple[ChatOutcome, ...]:
    return tuple(ChatFailure(custom_id, reason, batch_id, kind) for custom_id in custom_ids)


__all__ = ("BatchRun", "BatchStatus", "batch_body", "wire_custom_id")
