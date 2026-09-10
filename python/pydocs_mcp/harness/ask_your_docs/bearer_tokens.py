"""Bearer sources for the ask-your-docs chat model (LLM-connection design §4.4).

One ``BearerSource`` per auth identity holds the value that becomes
``Authorization: Bearer <value>``: nothing (``NoBearer``), an environment
variable (``EnvironmentKeyBearer``) or a token fetched from an internal token
service and renewed on demand (``TokenServiceBearer``). ``RenewOnStatusAuth``
is the ``httpx.Auth`` flow that renews on a ``401`` and re-sends the same
request once (the SDK itself never retries a 401); ``StripAuthorizationAuth``
removes the header for the no-auth case. ``redact_bearer``,
``translate_auth_errors`` and ``display_url`` are the redaction boundary every
auth failure crosses before a person, a tool result or a trace sees it (H4).

Light by contract: ``httpx`` (transitive via the required ``openai`` dep) at
module level; ``openai`` itself is imported function-locally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections.abc import AsyncGenerator, Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

# WHY these values: the envelope the capability probes use (multimodal.py
# _PROBE_*), defined again here on purpose — a later probe tuning must never
# silently change token fetching.
_TOKEN_FETCH_TIMEOUT_SECONDS = 5.0
_TOKEN_FETCH_ATTEMPTS = 3
_TOKEN_FETCH_BACKOFF_SECONDS = (2.0, 4.0)
# H3: a persistently rejecting endpoint must not turn every SDK attempt into a
# token fetch — a renew younger than this (measured between renew attempts,
# never against the first fetch) returns the cache.
_MIN_RENEW_INTERVAL_SECONDS = 5.0
_BEARER_PREFIX = "Bearer "
_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+")


class TokenServiceError(PydocsMCPError, RuntimeError):
    """The token service could not produce a token (design E1, E2, E3)."""


class BearerUnavailableError(PydocsMCPError, RuntimeError):
    """A configured environment key is unset (design E5)."""


class BearerRejectedError(PydocsMCPError, RuntimeError):
    """The endpoint rejected the bearer after the one renewal (design E4).

    Built WITHOUT the response body, which gateways fill with the presented
    credential (H4); ``detail`` carries a failed renewal's cause.
    """

    def __init__(
        self, *, status: int, host: str, last_four: str, detail: str | None = None
    ) -> None:
        self.status = status
        self.host = host
        self.last_four = last_four
        message = (
            f"endpoint {host} rejected bearer …{last_four} (status {status}); "
            "renew the token or check ask_your_docs.llm.auth"
        )
        if detail:
            message += f"; renew failed: {detail}"
        super().__init__(message)


# The bearer failures that are E1 / E4 / E5 — never a listing failure, never a
# rung failure, never cached as a verdict (design H3). ONE home, next to the
# three classes: the model listing and the capability ladder both import THIS
# tuple, so a fourth bearer error reaches every re-raise site at once.
BEARER_ERRORS: tuple[type[PydocsMCPError], ...] = (
    TokenServiceError,
    BearerUnavailableError,
    BearerRejectedError,
)


@dataclass(frozen=True, slots=True)
class BearerStatus:
    """What the UI may show — never the value itself (D4)."""

    auth_mode: AuthMode
    renewed_at: datetime | None
    last_four: str  # "" only when there is no bearer


@runtime_checkable
class BearerSource(Protocol):
    def current(self) -> str: ...  # the cached value; fetches lazily on the first call
    def peek(self) -> str: ...  # the cached value without fetching (redaction, the UI)
    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str: ...
    def describe(self) -> BearerStatus: ...


def last_four_of(token: str) -> str:
    """The last four characters (D4); ``""`` for an empty bearer."""
    return token[-4:]


def display_url(url: str) -> str:
    """``scheme://host[:port]/path`` — userinfo and query stripped (H4)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    prefix = f"{parts.scheme}://" if parts.scheme else ""
    return f"{prefix}{host}{parts.path}"


def display_host(base_url: str | None) -> str:
    """``host[:port]`` for the status line, or ``vendor default``."""
    if not base_url:
        return "vendor default"
    parts = urlsplit(base_url)
    host = parts.hostname or base_url
    return f"{host}:{parts.port}" if parts.port is not None else host


class NoBearer:
    """Null Object: no ``Authorization`` header at all (``AuthMode.NONE``)."""

    def current(self) -> str:
        return ""

    def peek(self) -> str:
        return ""

    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str:
        return ""

    def describe(self) -> BearerStatus:
        return BearerStatus(AuthMode.NONE, None, "")


# The ONE shared no-auth bearer. A module constant, not a `NoBearer()` call in a
# signature default (ruff B008): the Null Object is stateless, so one instance
# serves every seam, and every default points at the SAME object — build sites,
# the reinspect tool and the build context no longer each declare their own.
NO_BEARER: BearerSource = NoBearer()


class EnvironmentKeyBearer:
    """The bearer is an environment variable, re-read on every call.

    ``required=False`` is the lenient no-block form (an unset variable means
    no header, never an error — the SDK's own rule); ``required=True`` is the
    strict form for an explicit ``auth.api_key_env`` (design E5).
    """

    def __init__(self, var_name: str, *, required: bool) -> None:
        self.var_name = var_name
        self.required = required

    def current(self) -> str:
        value = os.environ.get(self.var_name, "")
        if not value and self.required:
            raise BearerUnavailableError(
                f"environment variable {self.var_name} is unset; "
                "ask_your_docs.llm.auth.api_key_env names it"
            )
        return value

    def peek(self) -> str:
        return os.environ.get(self.var_name, "")

    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str:
        return self.current()

    def describe(self) -> BearerStatus:
        return BearerStatus(AuthMode.ENV_KEY, None, last_four_of(self.peek()))


class TokenServiceBearer:
    """A token fetched from ``token_url`` on first use, cached, renewed on demand (R4)."""

    def __init__(
        self,
        token_url: str,
        *,
        token_field: str | None = None,
        transport: httpx.BaseTransport | None = None,  # test seam
        now: Callable[[], float] = time.monotonic,  # test seam (the renew interval)
        sleep: Callable[[float], None] = time.sleep,  # test seam (the fetch backoff)
    ) -> None:
        self.token_url = token_url
        self.token_field = token_field
        self.last_error: str | None = None
        self._transport = transport
        self._now = now
        self._sleep = sleep
        self._lock = threading.Lock()
        self._token = ""
        self._renewed_at: datetime | None = None
        self._renew_attempted_at: float | None = None

    def current(self) -> str:
        if self._token:
            return self._token
        with self._lock:  # a second concurrent first request waits, then reads the cache
            if not self._token:
                self._replace(self._fetch_recording(), reason="first_request")
            return self._token

    def peek(self) -> str:
        return self._token

    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str:
        with self._lock:
            if rejected is not None and self._token != rejected:
                return self._cache_hit()  # another flow renewed first (H3)
            if self._within_renew_interval():
                return self._cache_hit()  # a persistently rejecting endpoint (H3)
            self._renew_attempted_at = self._now()
            self._replace(self._fetch_recording(), reason=reason)
            return self._token

    def describe(self) -> BearerStatus:
        return BearerStatus(AuthMode.TOKEN_SERVICE, self._renewed_at, last_four_of(self._token))

    def _within_renew_interval(self) -> bool:
        if self._renew_attempted_at is None or not self._token:
            return False
        return self._now() - self._renew_attempted_at < _MIN_RENEW_INTERVAL_SECONDS

    def _cache_hit(self) -> str:
        self._log("bearer_renewed", reason="cache_hit")
        return self._token

    def _replace(self, token: str, *, reason: str) -> None:
        self._token = token
        self._renewed_at = datetime.now().astimezone()
        self._log(
            "bearer_fetched" if reason == "first_request" else "bearer_renewed", reason=reason
        )

    def _log(self, event: str, *, reason: str) -> None:
        # Never the token, never last_four, never the full URL (H4).
        log.info(
            json.dumps(
                {
                    "event": event,
                    "auth_mode": AuthMode.TOKEN_SERVICE.value,
                    "token_url_host": urlsplit(self.token_url).hostname,
                    "attempts": _TOKEN_FETCH_ATTEMPTS,
                    "renewed_at": self._renewed_at.isoformat() if self._renewed_at else None,
                    "reason": reason,
                }
            )
        )

    def _fetch_recording(self) -> str:
        try:
            token = self._fetch()
        except TokenServiceError as exc:
            self.last_error = str(exc)
            raise
        self.last_error = None
        return token

    def _fetch(self) -> str:
        # Performance: ONE client for the whole retry envelope. A fresh client per attempt
        # rebuilt httpx's SSL context (a certifi parse) and dropped keep-alive exactly when
        # a retry wants it — three times over against a token service that is down.
        with httpx.Client(
            timeout=_TOKEN_FETCH_TIMEOUT_SECONDS, transport=self._transport
        ) as client:
            return self._fetch_with_retries(client)

    def _fetch_with_retries(self, client: httpx.Client) -> str:
        last = "no attempt"
        for attempt in range(_TOKEN_FETCH_ATTEMPTS):
            try:
                response = client.get(self.token_url)
                response.raise_for_status()
                return self._parse_body(response)
            except httpx.HTTPStatusError as exc:
                last = f"status {exc.response.status_code}"
            except httpx.HTTPError as exc:
                last = exc.__class__.__name__
            if attempt < _TOKEN_FETCH_ATTEMPTS - 1:
                self._sleep(_TOKEN_FETCH_BACKOFF_SECONDS[min(attempt, 1)])
        raise TokenServiceError(
            f"token service {display_url(self.token_url)} unreachable after "
            f"{_TOKEN_FETCH_ATTEMPTS} attempts (last: {last})"
        )

    def _parse_body(self, response: httpx.Response) -> str:
        where = f"token service {display_url(self.token_url)}"
        content_type = response.headers.get("content-type", "unknown")
        if self.token_field is None:
            token = response.text.strip()  # the owner's requests.get(url).text
        else:
            token = _json_field(response, self.token_field, where, content_type)
        if not token:
            raise TokenServiceError(
                f"{where}: expected a non-empty token body, got empty (content-type {content_type})"
            )
        return token


def _json_field(response: httpx.Response, field: str, where: str, content_type: str) -> str:
    """The token under ``field`` — failures name the body's SHAPE, never its bytes (E2)."""
    try:
        payload = response.json()
    except ValueError:
        raise TokenServiceError(
            f"{where}: expected JSON body with field {field!r}, got non-JSON body "
            f"({content_type}, {len(response.content)} bytes)"
        ) from None
    if not isinstance(payload, dict) or field not in payload:
        keys = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        raise TokenServiceError(
            f"{where}: expected JSON body with field {field!r}, got keys={keys}"
        )
    return str(payload[field]).strip()


def _bearer_in(request: httpx.Request) -> str:
    return request.headers.get("Authorization", "").removeprefix(_BEARER_PREFIX)


class RenewOnStatusAuth(httpx.Auth):
    """Renew the bearer on a status in ``statuses`` and re-send the SAME request once (R4).

    The header on the first pass comes from the SDK's callable ``api_key``;
    this flow only rewrites it after a renewal. The rejected value is parsed
    from the request itself so the compare-and-swap sees the token the
    endpoint actually rejected. A renewal that fails (token service down, or an
    environment key that went away) is not raised out of ``send`` — the SDK
    would retry the whole request — the rejected response is returned and the
    cause rides ``bearer.last_error``. The catch is ``BEARER_ERRORS``, not one
    member of it: this flow is typed against the ``BearerSource`` Protocol, so
    ANY bearer failure has to stay inside ``send`` for the contract to hold.
    """

    def __init__(self, bearer: BearerSource, statuses: tuple[int, ...]) -> None:
        self.bearer = bearer
        self.statuses = statuses

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        response = yield request
        if response.status_code not in self.statuses:
            return
        try:
            renewed = self.bearer.renew(_bearer_in(request), reason="rejected_status")
        except BEARER_ERRORS:
            return
        request.headers["Authorization"] = _BEARER_PREFIX + renewed
        yield request

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        response = yield request
        if response.status_code not in self.statuses:
            return
        try:  # the bounded fetch never blocks the event loop (CLAUDE.md §Async Patterns)
            renewed = await asyncio.to_thread(
                self.bearer.renew, _bearer_in(request), reason="rejected_status"
            )
        except BEARER_ERRORS:
            return
        request.headers["Authorization"] = _BEARER_PREFIX + renewed
        yield request


class StripAuthorizationAuth(httpx.Auth):
    """No ``Authorization`` header on the wire (``AuthMode.NONE``)."""

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers.pop("Authorization", None)
        yield request


def redact_bearer(text: str, bearer: BearerSource) -> str:
    """Mask the bearer's cached value and any ``Bearer <x>`` pattern in ``text`` (H4)."""
    token = bearer.peek()
    mask = f"…{last_four_of(token)}" if token else "…"
    if token:
        text = text.replace(token, mask)
    return _BEARER_PATTERN.sub(f"Bearer {mask}", text)


def redacted_failure_caption(exc: Exception, bearer: BearerSource) -> str:
    """``Class: message`` with every bearer masked — the ONE shape a failure reaches a person in.

    The connection test's caption and the model listing's non-fatal ``error`` are
    the same sentence; keeping one builder means a future widening of redaction
    (a second credential shape, say) reaches both without being remembered twice.
    """
    return f"{exc.__class__.__name__}: {redact_bearer(str(exc), bearer)}"


@contextmanager
def translate_auth_errors(bearer: BearerSource) -> Iterator[None]:
    """Turn the SDK's 401/403 errors into ``BearerRejectedError`` without the body (E4, H4).

    Wraps every consumer of a factory-built model, for ALL auth modes: with
    an environment key or no auth there is no renewing flow, so a 401 is a
    raw SDK error that would otherwise carry the response body.
    """
    import openai  # heavy; function-local by contract

    try:
        yield
    except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
        host = exc.response.url.host if exc.response is not None else "endpoint"
        raise BearerRejectedError(
            status=exc.status_code,
            host=host,
            last_four=last_four_of(bearer.peek()),
            detail=getattr(bearer, "last_error", None),
        ) from None


__all__ = (
    "BEARER_ERRORS",
    "NO_BEARER",
    "BearerRejectedError",
    "BearerSource",
    "BearerStatus",
    "BearerUnavailableError",
    "EnvironmentKeyBearer",
    "NoBearer",
    "RenewOnStatusAuth",
    "StripAuthorizationAuth",
    "TokenServiceBearer",
    "TokenServiceError",
    "display_host",
    "display_url",
    "last_four_of",
    "redact_bearer",
    "redacted_failure_caption",
    "translate_auth_errors",
)
