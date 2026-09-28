"""One OpenRouter call, made the way every judge role makes it.

The bearer is read from the environment when the call is made, and no error or
log line carries it. A timeout or a 5xx is retried a bounded number of times,
with backoff; a 4xx never is, because repeating a refused request cannot change
the answer. The model that answered is checked against the pin, so a silently
swapped model never scores an answer.

Example:
    >>> served_model_matches("jev-1.13", "typesafe/jev-1.13-20260917")
    True
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

from pydocs_eval.judge.role_config import JudgeConfigError

log = logging.getLogger(__name__)

_BACKOFF_SECONDS = 0.5
_SERVER_ERROR = 500
_TOO_MANY_REQUESTS = 429
_ERROR_EXCERPT_CHARS = 300
_BEARER_HEADER = re.compile(r"Bearer\s+\S+")
# The dated snapshot OpenRouter names after a pinned id: -20260917 or -2026-09-17.
_DATED_SNAPSHOT = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})")
_VARIANT_SEPARATOR = ":"
_NAMESPACE_SEPARATOR = "/"


class JudgeUnavailableError(Exception):
    """The judge service gave no answer: an outage, booked ``undefined``, never 0."""


class JudgeRequestError(Exception):
    """The judge service refused the request (a 4xx other than 429): the request is wrong."""


class JudgeResponseError(Exception):
    """A judge service answered in a shape its documentation does not describe."""


class JudgeModelMismatchError(Exception):
    """The model that answered is not the pinned one: its thresholds were never fitted to it."""

    def __init__(self, *, model: str, pinned: str) -> None:
        super().__init__(f"judge model mismatch: got {model!r}, expected {pinned!r}")


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


def bearer_from_env(api_key_env: str) -> str:
    """The bearer in ``$api_key_env``, read now, or a refusal naming the variable."""
    bearer = os.environ.get(api_key_env, "").strip()
    if not bearer:
        raise JudgeConfigError(f"${api_key_env} is not set: export the OpenRouter key first")
    return bearer


def post_json(
    http: httpx.Client, url: str, body: Mapping[str, object], *, bearer: str, policy: CallPolicy
) -> object:
    """POST ``body`` as JSON (keys in their given order) and return the parsed answer."""
    content = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {**_auth_headers(bearer), "Content-Type": "application/json"}
    response = send_with_retries(
        lambda: http.post(url, content=content, headers=headers, timeout=policy.timeout_seconds),
        bearer=bearer,
        policy=policy,
    )
    return _json_of(response, bearer=bearer, label=policy.label)


def get_json(http: httpx.Client, url: str, *, bearer: str, policy: CallPolicy) -> object:
    """GET ``url`` and return the parsed answer."""
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
        excerpt = redact(response.text[:_ERROR_EXCERPT_CHARS], bearer)
        raise JudgeRequestError(f"{label}: HTTP {response.status_code}: {excerpt}")
    return response


def _json_of(response: httpx.Response, *, bearer: str, label: str) -> object:
    try:
        return response.json()
    except ValueError:
        excerpt = redact(response.text[:_ERROR_EXCERPT_CHARS], bearer)
        raise JudgeResponseError(f"{label}: answered with non-JSON: {excerpt!r}") from None


def _auth_headers(bearer: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer}"}


def _log_retryable_failure(label: str, attempt: int, failure: str) -> None:
    fields = {"event": "judge_call_failed", "role": label, "attempt": attempt + 1}
    log.warning(json.dumps({**fields, "reason": failure}))


def redact(text: str, bearer: str) -> str:
    """``text`` with the bearer and any ``Bearer <token>`` pattern masked.

    Example:
        >>> redact("Bearer sk-1 and sk-1", "sk-1")
        'Bearer … and …'
    """
    masked = text.replace(bearer, "…") if bearer else text
    return _BEARER_HEADER.sub("Bearer …", masked)


def check_served_model(pinned: str, served: str, *, bearer: str = "") -> None:
    """Raise unless ``served`` is the model ``pinned`` names (:func:`served_model_matches`)."""
    if not served_model_matches(pinned, served):
        raise JudgeModelMismatchError(model=redact(served, bearer), pinned=pinned)


def served_model_matches(pinned: str, served: str) -> bool:
    """Whether ``served`` names the model ``pinned`` names: that version, never another.

    OpenRouter reports the id of the model that served a call, which differs from
    the pin in three documented ways only: it namespaces a bare System One id
    (``jev-1.13`` is served as ``typesafe/jev-1.13``), it names the dated
    snapshot that answered (``…-20260917``), and it drops the endpoint variant a
    request selects (``:batch``). Any other difference is another model.

    Example:
        >>> served_model_matches("jev-1.13", "typesafe/jev-1.14-20261001")
        False
    """
    base, served_base = _without_variant(pinned), _without_variant(served)
    if _NAMESPACE_SEPARATOR not in base:
        served_base = served_base.rpartition(_NAMESPACE_SEPARATOR)[2]
    if served_base == base:
        return True
    suffix = served_base.removeprefix(base)
    return suffix != served_base and _DATED_SNAPSHOT.fullmatch(suffix) is not None


def _without_variant(model: str) -> str:
    return model.split(_VARIANT_SEPARATOR, 1)[0]


__all__ = (
    "CallPolicy",
    "JudgeModelMismatchError",
    "JudgeRequestError",
    "JudgeResponseError",
    "JudgeUnavailableError",
    "bearer_from_env",
    "check_served_model",
    "get_json",
    "post_json",
    "redact",
    "send_with_retries",
    "served_model_matches",
)
