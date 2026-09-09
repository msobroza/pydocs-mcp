"""Model discovery for the Connection dialog and the endpoint probe (LLM-connection design §4.6).

``GET {base_url}/models`` through an ``openai.AsyncOpenAI`` built from the
client factory's one auth decision — the same bearer, timeout and renewing
``Auth`` as the chat model — cached briefly per ``(base_url, auth identity)``
and failing soft (a listing is a convenience, not a gate; D5, R5).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerRejectedError,
    BearerSource,
    BearerUnavailableError,
    TokenServiceError,
    display_host,
    redact_bearer,
    translate_auth_errors,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    LlmConnection,
    async_httpx_client,
    connection_auth_kwargs,
    connection_identity,
)

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

_LISTING_TIMEOUT_SECONDS = 10.0
_LISTING_MAX_RETRIES = 1
_MODEL_LISTING_TTL_SECONDS = 60.0
# Bearer failures are E1 / E4 / E5, never a listing failure (design §4.6).
_BEARER_ERRORS = (TokenServiceError, BearerUnavailableError, BearerRejectedError)

ListModels = Callable[[LlmConnection, BearerSource], Awaitable[list[dict]]]


@dataclass(frozen=True, slots=True)
class ModelListing:
    model_ids: tuple[str, ...]
    error: str | None  # non-fatal: shown in the dialog caption (E6)
    fetched_at: float  # the injected clock's value


def _async_key(api_key: Any) -> Any:
    """The async SDK client AWAITS its api_key provider (openai/_client.py:649-651)."""
    if not callable(api_key):
        return api_key

    async def provider() -> str:
        # The first lazy token fetch is blocking I/O — keep it off the event loop,
        # as langchain-openai does for the async client (test_sdk_pins' fifth pin).
        return await asyncio.to_thread(api_key)

    return provider


def _listing_client(connection: LlmConnection, bearer: BearerSource, transport: Any) -> Any:
    """The SDK client for one listing: the factory's auth decision, a timeout, bounded retries."""
    from openai import AsyncOpenAI  # heavy; lazy by contract

    api_key, auth = connection_auth_kwargs(connection, bearer, tolerate_missing_key=True)
    return AsyncOpenAI(
        base_url=connection.base_url,
        api_key=_async_key(api_key),
        http_client=async_httpx_client(auth, transport),
        timeout=_LISTING_TIMEOUT_SECONDS,
        max_retries=_LISTING_MAX_RETRIES,
    )


async def fetch_models_payload(
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    list_models: ListModels | None = None,
    transport: Any = None,
) -> list[dict]:
    """``GET {base_url}/models`` with the connection's bearer; entries as plain dicts."""
    if list_models is not None:
        return await list_models(connection, bearer)
    client = _listing_client(connection, bearer, transport)
    with translate_auth_errors(bearer):
        page = await client.models.list()
    # Every field the endpoint sent, not just `id`: rung 3's _entry_hints_vision
    # reads the metadata ones (capabilities / modality / architecture / tags).
    return [entry.to_dict() for entry in page.data]


async def fetch_model_ids(
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    list_models: ListModels | None = None,
    transport: Any = None,
    now: Callable[[], float] = time.monotonic,
) -> ModelListing:
    """The listing record — sorted unique ids, or a non-fatal ``error`` (E6)."""
    try:
        payload = await fetch_models_payload(
            connection, bearer, list_models=list_models, transport=transport
        )
    except _BEARER_ERRORS:
        raise
    except Exception as exc:  # broad on purpose — E6: the dialog falls back to a text field
        return ModelListing((), _log_listing_failure(connection, bearer, exc), now())
    return _listing_from_payload(payload, now)


def _log_listing_failure(connection: LlmConnection, bearer: BearerSource, exc: Exception) -> str:
    """The one redacted reason, logged once and shown in the dialog's caption (H4)."""
    error = f"{exc.__class__.__name__}: {redact_bearer(str(exc), bearer)}"
    log.warning(
        json.dumps(
            {
                "event": "model_listing_failed",
                "endpoint": display_host(connection.base_url),
                "error": error,
            }
        )
    )
    return error


def _listing_from_payload(payload: list[dict], now: Callable[[], float]) -> ModelListing:
    """Sorted unique ids; entries with no ``id`` at all are an (also non-fatal) shape error."""
    ids = sorted({str(e["id"]) for e in payload if isinstance(e, dict) and e.get("id")})
    if ids or not payload:
        return ModelListing(tuple(ids), None, now())
    return ModelListing((), _unexpected_payload_message(payload[0]), now())


def _unexpected_payload_message(first_entry: object) -> str:
    """Name the SHAPE the endpoint sent instead — never its bytes (H4), like E2's token errors."""
    keys = sorted(first_entry) if isinstance(first_entry, dict) else type(first_entry).__name__
    return f"unexpected /models payload: expected {{'data': [{{'id': ...}}]}}, got keys={keys}"


# One process-level cache keyed on (base_url, auth identity); the dialog's
# refresh action and a Renew evict through clear_model_listing_cache.
_listing_cache: dict[tuple, ModelListing] = {}


def _cache_key(connection: LlmConnection) -> tuple:
    return (connection.base_url, connection_identity(connection))


async def cached_model_listing(
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    now: Callable[[], float] = time.monotonic,
    list_models: ListModels | None = None,
    transport: Any = None,
) -> ModelListing:
    """The cached listing for this connection, refetched once the TTL has passed."""
    key = _cache_key(connection)
    cached = _listing_cache.get(key)
    if cached is not None and now() - cached.fetched_at < _MODEL_LISTING_TTL_SECONDS:
        return cached
    listing = await fetch_model_ids(
        connection, bearer, list_models=list_models, transport=transport, now=now
    )
    _listing_cache[key] = listing
    return listing


def clear_model_listing_cache(connection: LlmConnection | None = None) -> None:
    """Evict one connection's entry, or everything (the test seam, like ``clear_detection_cache``)."""
    if connection is None:
        _listing_cache.clear()
        return
    _listing_cache.pop(_cache_key(connection), None)


__all__ = (
    "ModelListing",
    "cached_model_listing",
    "clear_model_listing_cache",
    "fetch_model_ids",
    "fetch_models_payload",
)
