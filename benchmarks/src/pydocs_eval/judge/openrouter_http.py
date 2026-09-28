"""One OpenRouter call, made the way every judge role makes it.

The bearer is read from the environment when the call is made, and no error or
log line carries it. A timeout or a 5xx is retried a bounded number of times,
with backoff; a 4xx never is, because repeating a refused request cannot change
the answer. The model that answered is checked against the pin, so a silently
swapped model never scores an answer.

Example:
    >>> redact("Authorization: Bearer sk-or-1", "sk-or-1")
    'Authorization: Bearer …'
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

from pydocs_eval.judge.judge_errors import (
    JudgeConfigError,
    JudgeModelMismatchError,
    JudgeRequestError,
    JudgeResponseError,
    JudgeUnavailableError,
)
from pydocs_eval.judge.model_ids import served_model_matches
from pydocs_eval.judge.role_config import OpenRouterCallConfig

log = logging.getLogger(__name__)

_BACKOFF_SECONDS = 0.5
_SERVER_ERROR = 500
_TOO_MANY_REQUESTS = 429
#: How much of an answer an error quotes, redacted.
ERROR_EXCERPT_CHARS = 300
_BEARER_HEADER = re.compile(r"Bearer\s+\S+")


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


def send_with_retries(
    send: Callable[[], httpx.Response], *, bearer: str, policy: CallPolicy
) -> httpx.Response:
    """``send()`` once, then again only after a timeout or a 5xx, at most ``policy.retries`` times.

    Example:
        >>> send_with_retries(lambda: http.get(url), bearer=bearer, policy=policy)  # doctest: +SKIP
        <Response [200 OK]>

    Raises:
        JudgeUnavailableError: no answer after every attempt, a transport failure,
            or a rate limit (never retried: it is not a 5xx).
        JudgeRequestError: any other 4xx.
    """
    failure = ""
    for attempt in range(policy.retries + 1):
        if attempt:
            policy.sleep(_BACKOFF_SECONDS * 2 ** (attempt - 1))
        response, failure = _attempt(send, bearer=bearer, label=policy.label)
        if response is not None:
            return _accepted(response, bearer=bearer, label=policy.label)
        _log_retryable_failure(policy.label, attempt, failure)
    raise JudgeUnavailableError(
        f"{policy.label}: no answer after {policy.retries + 1} attempts ({failure})"
    )


def _attempt(
    send: Callable[[], httpx.Response], *, bearer: str, label: str
) -> tuple[httpx.Response | None, str]:
    """One try: its response, or ``None`` and why the try may be retried."""
    try:
        response = send()
    except httpx.TimeoutException as exc:
        return None, f"timed out: {type(exc).__name__}"
    except httpx.TransportError as exc:
        detail = redact(str(exc), bearer)
        raise JudgeUnavailableError(f"{label}: {type(exc).__name__}: {detail}") from None
    if response.status_code >= _SERVER_ERROR:
        return None, f"HTTP {response.status_code}"
    return response, ""


def _accepted(response: httpx.Response, *, bearer: str, label: str) -> httpx.Response:
    """``response`` when it answered; a refusal raises with a redacted excerpt of its body."""
    if response.status_code == _TOO_MANY_REQUESTS:
        raise JudgeUnavailableError(f"{label}: rate limited (HTTP 429)")
    if response.is_error:
        excerpt = redact(response.text[:ERROR_EXCERPT_CHARS], bearer)
        raise JudgeRequestError(
            f"{label}: HTTP {response.status_code}: {excerpt}", status_code=response.status_code
        )
    return response


def _json_of(response: httpx.Response, *, bearer: str, label: str) -> object:
    try:
        return response.json()
    except ValueError:
        excerpt = redact(response.text[:ERROR_EXCERPT_CHARS], bearer)
        raise JudgeResponseError(f"{label}: answered with non-JSON: {excerpt!r}") from None


def _auth_headers(bearer: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer}"}


def _log_retryable_failure(label: str, attempt: int, failure: str) -> None:
    fields = {"event": "judge_call_failed", "role": label, "attempt": attempt + 1}
    log.warning(json.dumps({**fields, "reason": failure}))


def redact(text: str, bearer: str) -> str:
    """``text`` with the bearer and any ``Bearer <token>`` pattern masked.

    Example:
        >>> redact("sk-1 and sk-1", "sk-1")
        '… and …'
    """
    masked = text.replace(bearer, "…") if bearer else text
    return _BEARER_HEADER.sub("Bearer …", masked)


def check_served_model(pinned: str, served: str, *, bearer: str = "") -> None:
    """Raise unless ``served`` is the model ``pinned`` names (``model_ids.served_model_matches``).

    Example:
        >>> check_served_model("jev-1.13", "typesafe/jev-1.13-20260917")
    """
    if not served_model_matches(pinned, served):
        raise JudgeModelMismatchError(model=redact(served, bearer), pinned=pinned)


def usage_cost(body: Mapping[str, object]) -> float | None:
    """What OpenRouter says an answer cost (``usage.cost``); ``None`` when it does not say.

    Example:
        >>> usage_cost({"usage": {"cost": 0.00002}}), usage_cost({"usage": {"cost": True}})
        (2e-05, None)
    """
    usage = body.get("usage")
    cost = usage.get("cost") if isinstance(usage, Mapping) else None
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        return None
    return float(cost)


__all__ = (
    "ERROR_EXCERPT_CHARS",
    "CallPolicy",
    "bearer_from_env",
    "check_served_model",
    "get_json",
    "post_json",
    "redact",
    "route_url",
    "send_with_retries",
    "usage_cost",
)
