"""One OpenRouter call, made the way every judge role makes it.

The bearer is read from the environment when the call is made, and no error or
log line carries it. A timeout, a 5xx or a rate limit (429) is retried a bounded
number of times, with backoff stretched to the ``Retry-After`` the service asks
for; any other 4xx never is, because repeating a refused request cannot change
the answer. Reading a body, and quoting one in an error, is ``openrouter_body``'s.

Example:
    >>> CallPolicy.of("judge.jev.model", OpenRouterCallConfig(timeout_seconds=10.0), print).retries
    2
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

from pydocs_eval.judge.judge_errors import (
    JudgeConfigError,
    JudgeRequestError,
    JudgeResponseError,
    JudgeUnavailableError,
)
from pydocs_eval.judge.openrouter_body import redact, redacted_excerpt
from pydocs_eval.judge.role_config import OpenRouterCallConfig

log = logging.getLogger(__name__)

_BACKOFF_SECONDS = 0.5
# A rate limit asks for a pause, not a stall: past this, the next try is made anyway
# and a limit that persists reads as an outage.
_MAX_WAIT_SECONDS = 30.0
_TOO_MANY_REQUESTS = 429
# Any status from here up is retried, a non-standard one past 599 included:
# it must never be parsed, let alone cached, as an answer.
_SERVER_ERROR_FLOOR = 500


@dataclass(frozen=True, slots=True)
class CallPolicy:
    """How one role calls: within ``timeout_seconds``, retried at most ``retries`` times.

    ``label`` names the role in every error and log line; ``sleep`` is the backoff
    clock, injectable so a test never waits.
    """

    label: str
    timeout_seconds: float
    retries: int
    sleep: Callable[[float], None]

    @classmethod
    def of(
        cls, label: str, config: OpenRouterCallConfig, sleep: Callable[[float], None]
    ) -> CallPolicy:
        """The policy a role block sets: its timeout and its retries."""
        return cls(label, config.timeout_seconds, config.retries, sleep)


def route_url(config: OpenRouterCallConfig, route: str) -> str:
    """``route`` under the role's endpoint: ``route_url(jev, "/systemone")``.

    Example:
        >>> route_url(OpenRouterCallConfig(timeout_seconds=10.0), "/systemone")
        'https://openrouter.ai/api/v1/systemone'
    """
    return f"{config.endpoint.rstrip('/')}{route}"


def bearer_from_env(api_key_env: str) -> str:
    """The bearer in ``$api_key_env``, read now, or a refusal naming the variable.

    Example:
        >>> bearer_from_env("OPENROUTER_API_KEY")  # doctest: +SKIP
        'sk-or-v1-…'
    """
    bearer = os.environ.get(api_key_env, "").strip()
    if not bearer:
        raise JudgeConfigError(f"${api_key_env} is not set: export the OpenRouter key first")
    return bearer


def post_json(
    http: httpx.Client, url: str, body: Mapping[str, object], *, bearer: str, policy: CallPolicy
) -> object:
    """POST ``body`` as JSON (keys in their given order) and return the parsed answer.

    Example:
        >>> post_json(http, url, {"model": "jev-1.13"}, bearer=bearer, policy=policy)  # doctest: +SKIP
        {'model': 'typesafe/jev-1.13-20260917', ...}
    """
    content = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {**_auth_headers(bearer), "Content-Type": "application/json"}
    response = send_with_retries(
        lambda: http.post(url, content=content, headers=headers, timeout=policy.timeout_seconds),
        bearer=bearer,
        policy=policy,
    )
    return _json_of(response, bearer=bearer, label=policy.label)


def get_json(http: httpx.Client, url: str, *, bearer: str, policy: CallPolicy) -> object:
    """GET ``url`` and return the parsed answer.

    Example:
        >>> get_json(http, f"{url}/batch_123", bearer=bearer, policy=policy)["status"]  # doctest: +SKIP
        'in_progress'
    """
    response = send_with_retries(
        lambda: http.get(url, headers=_auth_headers(bearer), timeout=policy.timeout_seconds),
        bearer=bearer,
        policy=policy,
    )
    return _json_of(response, bearer=bearer, label=policy.label)


def delete_url(http: httpx.Client, url: str, *, bearer: str, policy: CallPolicy) -> None:
    """DELETE ``url``: an idempotent call, retried like a GET.

    Example:
        >>> delete_url(http, f"{url}/batch_123", bearer=bearer, policy=policy)  # doctest: +SKIP
    """
    send_with_retries(
        lambda: http.delete(url, headers=_auth_headers(bearer), timeout=policy.timeout_seconds),
        bearer=bearer,
        policy=policy,
    )


def send_with_retries(
    send: Callable[[], httpx.Response], *, bearer: str, policy: CallPolicy
) -> httpx.Response:
    """``send()`` once, then again after a timeout, a 5xx or a 429, at most ``policy.retries`` times.

    Example:
        >>> send_with_retries(lambda: http.get(url), bearer=bearer, policy=policy)  # doctest: +SKIP
        <Response [200 OK]>

    Raises:
        JudgeUnavailableError: no answer after every attempt, or a transport failure.
        JudgeRequestError: any other 4xx.
    """
    retry = _Retry("")
    for attempt in range(policy.retries + 1):
        if attempt:
            policy.sleep(_wait_before(attempt, retry))
        outcome = _attempt(send, bearer=bearer, label=policy.label)
        if isinstance(outcome, httpx.Response):
            return _accepted(outcome, bearer=bearer, label=policy.label)
        retry = outcome
        _log_retryable_failure(policy.label, attempt, retry.reason)
    raise JudgeUnavailableError(
        f"{policy.label}: no answer after {policy.retries + 1} attempts ({retry.reason})"
    )


@dataclass(frozen=True, slots=True)
class _Retry:
    """Why one try may be retried, and how long the service asked to wait first."""

    reason: str
    wait_seconds: float = 0.0


def _attempt(
    send: Callable[[], httpx.Response], *, bearer: str, label: str
) -> httpx.Response | _Retry:
    """One try: its response, or why it may be retried."""
    try:
        response = send()
    except httpx.TimeoutException as exc:
        return _Retry(f"timed out: {type(exc).__name__}")
    except httpx.TransportError as exc:
        detail = redact(str(exc), bearer)
        raise JudgeUnavailableError(f"{label}: {type(exc).__name__}: {detail}") from None
    if response.status_code == _TOO_MANY_REQUESTS:
        return _Retry("rate limited (HTTP 429)", _retry_after_seconds(response))
    if response.status_code >= _SERVER_ERROR_FLOOR:
        return _Retry(f"HTTP {response.status_code}")
    return response


def _wait_before(attempt: int, retry: _Retry) -> float:
    """The backoff before ``attempt``, stretched to what the service asked, within the cap."""
    backoff = _BACKOFF_SECONDS * 2.0 ** (attempt - 1)
    return min(max(backoff, retry.wait_seconds), _MAX_WAIT_SECONDS)


def _retry_after_seconds(response: httpx.Response) -> float:
    """``Retry-After`` as a number of seconds; 0 when absent, negative, or a date.

    The HTTP-date form falls back to the backoff: reading it right would need a
    clock, for a form neither TypeSafe nor OpenRouter documents sending.
    """
    header: str = response.headers.get("retry-after", "")
    try:
        seconds = float(header)
    except ValueError:
        return 0.0
    return max(0.0, seconds)


def _accepted(response: httpx.Response, *, bearer: str, label: str) -> httpx.Response:
    """``response`` when it answered; a refusal raises with a redacted excerpt of its body."""
    if response.is_error:
        excerpt = redacted_excerpt(response.text, bearer)
        raise JudgeRequestError(
            f"{label}: HTTP {response.status_code}: {excerpt}",
            status_code=response.status_code,
            detail=excerpt,
        )
    return response


def _json_of(response: httpx.Response, *, bearer: str, label: str) -> object:
    try:
        return response.json()
    except ValueError:
        excerpt = redacted_excerpt(response.text, bearer)
        raise JudgeResponseError(f"{label}: answered with non-JSON: {excerpt!r}") from None


def _auth_headers(bearer: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer}"}


def _log_retryable_failure(label: str, attempt: int, failure: str) -> None:
    fields = {
        "event": "judge_call_failed",
        "role": label,
        "attempt": attempt + 1,
        "reason": failure,
    }
    log.warning(json.dumps(fields))


__all__ = (
    "CallPolicy",
    "bearer_from_env",
    "delete_url",
    "get_json",
    "post_json",
    "route_url",
    "send_with_retries",
)
