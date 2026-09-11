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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydocs_mcp.exceptions import PydocsMCPError

# BEARER_ERRORS is imported, never re-listed here: the capability ladder
# re-raises the SAME tuple, so a fourth bearer error cannot be honored by one
# and swallowed into a ModelListing(error=…) by the other (design H3, §4.6).
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BEARER_ERRORS,
    BearerSource,
    display_host,
    translate_auth_errors,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    LlmConnection,
    async_httpx_client,
    connection_auth_kwargs,
    connection_identity,
)

# The seam vocabulary has one home too — multimodal owns the rung types, and
# rung 3's seam IS this module's ``list_models`` seam, entry type included. No
# module-level cycle: multimodal imports this module function-locally.
from pydocs_mcp.harness.ask_your_docs.multimodal import ListingEntries, ListModels

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

_LISTING_TIMEOUT_SECONDS = 10.0
_LISTING_MAX_RETRIES = 1
_MODEL_LISTING_TTL_SECONDS = 60.0
_EXPECTED_LISTING_SHAPE = "expected {'data': [{'id': ...}]}"


class UnexpectedListingPayloadError(PydocsMCPError, RuntimeError):
    """A 200 from ``/models`` whose body is not a listing at all (E6).

    Carries the body's SHAPE — its top-level keys, or the type it arrived as when
    it is not an object at all — never its bytes (H4), the way E2's token-service
    errors do. ``fetch_model_ids`` turns it into a non-fatal caption; the
    capability ladder lets it fall through like any other rung-3 failure.
    """


@dataclass(frozen=True, slots=True)
class ModelListing:
    """One ``/models`` result for the Connection dialog: the ids to offer, or why it has none.

    ``error`` is non-fatal by contract (E6) — the dialog captions it and falls
    back to a free-text model field. Empty ``model_ids`` with ``error=None`` is
    the different, honest case of an endpoint that lists no models.
    """

    model_ids: tuple[str, ...]
    error: str | None  # non-fatal: shown in the dialog caption (E6)
    fetched_at: float  # the injected clock's value
    # Every entry the endpoint sent, by id — the dialog's display profile and control support
    # read them (model-params v2 §2-§3). LAST with a default so positional builds keep working;
    # compare=False so a listing still equals one built from the same ids.
    entries_by_id: Mapping[str, Mapping[str, Any]] = field(default_factory=dict, compare=False)


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
) -> ListingEntries:
    """``GET {base_url}/models`` with the connection's bearer; entries as plain dicts — bar an
    out-of-contract entry that is not an object, which passes through unconverted
    (``_entry_as_dict``) so the caller can name its type. Rung 3 skips any non-dict entry."""
    if list_models is not None:
        return await list_models(connection, bearer)
    client = _listing_client(connection, bearer, transport)
    with translate_auth_errors(bearer):
        page = await _listing_page(client)
    # Every field the endpoint sent, not just `id`: rung 3's _entry_hints_vision
    # reads the metadata ones (capabilities / modality / architecture / tags).
    return [_entry_as_dict(entry) for entry in _page_entries(page)]


async def _listing_page(client: Any) -> Any:
    """The 200's page, with the body's SHAPE checked BEFORE the SDK builds one from it (E6).

    ``client.models.list()`` parses inside itself, so a top-level body that is not
    a JSON object — a bare array, the HTML or plain text a wrong ``base_url``
    serves, an empty body — raises a bare ``AttributeError`` / ``JSONDecodeError``
    inside that call — upstream of every ``page.data`` guard — and the broad
    handler stringified it into the caption. ``with_raw_response`` hands the
    response over unparsed so the shape can be named first; a status or network
    failure still raises out of the call and keeps its ``_failure_reason`` caption.
    """
    raw = await client.models.with_raw_response.list()
    detail = _non_object_body_detail(raw.http_response)
    if detail is not None:
        raise UnexpectedListingPayloadError(_unexpected_payload_message(detail))
    return raw.parse()


def _non_object_body_detail(response: Any) -> str | None:
    """The SHAPE a 200 sent instead of the JSON object a listing owes — ``None`` when it sent one.

    Names the shape only, never the bytes (H4): a wrong ``base_url`` typically
    answers with a login page, whose text could carry anything.
    """
    if not response.content.strip():
        return "got an empty body"
    body = _sdk_decoded_body(response)
    if isinstance(body, dict):
        return None
    name = type(body).__name__
    return f"got {_indefinite_article(name)} {name} body"


def _sdk_decoded_body(response: Any) -> Any:
    """The value the SDK's parse would build the page from: the decoded JSON, else the raw text.

    Mirrors ``openai._legacy_response``: because the cast type is a model, it
    tries ``response.json()`` whatever the content type claims and falls back to
    the text when that fails. So a proxy answering ``text/html`` with a real
    listing still reads (this must NOT be rejected), while HTML, plain text and
    malformed JSON arrive as a ``str`` here exactly as they do there.
    """
    try:
        return response.json()
    except ValueError:  # json.JSONDecodeError; the SDK swallows it the same way
        return response.text


def _indefinite_article(word: str) -> str:
    """``an`` before a vowel — the caption reads ``an int body``, not ``a int body``."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def _page_entries(page: Any) -> ListingEntries:
    """The 200's entries, or the shape error a body that is not a listing earns (E6)."""
    if isinstance(page.data, list):
        return page.data
    # A 200 with no `data` list leaves page.data None — iterating it would
    # surface a bare TypeError in the dialog's caption instead of the shape.
    raise UnexpectedListingPayloadError(_unexpected_payload_message(_body_detail(page)))


def _entry_as_dict(entry: Any) -> Any:
    """One listing entry as a plain dict — an entry the SDK cannot convert passes through.

    Two out-of-contract bodies land here and NEITHER may reach the caption as a
    Python error (E6). Entries that are not objects at all (``{"data": ["a"]}``,
    ``[null]``, ``[[1, 2]]``) carry no ``to_dict``, so they pass through and
    ``_entries_detail`` names their type. An entry whose ``id`` is not a string
    makes the SDK's serializer emit a pydantic ``UserWarning``; the suite runs
    ``-W error``, so converting it plainly would caption that warning in the gate
    while production quietly read the id — ``warnings=False`` (pinned in
    test_sdk_pins) keeps both modes on the same shape message.
    """
    to_dict = getattr(entry, "to_dict", None)
    if not callable(to_dict):
        return entry
    return to_dict(warnings=False)


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
        # Inside the try: an out-of-contract payload (a mapping, nothing) raises here too (E6).
        return _listing_from_payload(connection, payload, now)
    except BEARER_ERRORS:
        raise
    except Exception as exc:  # broad on purpose — E6: the dialog falls back to a text field
        caption = _log_listing_failure(connection, _failure_reason(exc))
        return ModelListing((), caption, now())


def _failure_reason(exc: Exception) -> str:
    """The caption for one non-fatal failure: a shape message verbatim, else the class (+ status).

    A shape message is already H4-safe (keys and type names) and carries no class
    name — the dialog shows the endpoint's mistake, not ours.

    Never ``str(exc)`` for anything else: the SDK carries the upstream RESPONSE BODY in
    its message, and redaction cannot save it — masking the bearer's CURRENT value plus a
    ``Bearer <x>`` pattern leaves a body that quotes a since-renewed token BARE verbatim,
    in the caption a person reads AND in the WARNING record. The class and the status are
    the whole actionable part anyway; the body is the endpoint's prose about our secret.
    """
    if isinstance(exc, UnexpectedListingPayloadError):
        return str(exc)
    name = exc.__class__.__name__
    # openai.APIStatusError carries status_code; transport failures have none, and
    # "(status None)" would read as a status the endpoint never sent.
    status = getattr(exc, "status_code", None)
    return f"{name} (status {status})" if isinstance(status, int) else name


def _log_listing_failure(connection: LlmConnection, reason: str) -> str:
    """Log the one body-free reason — shape errors included — and hand it back (H4).

    ``reason`` is a class name (with the HTTP status when there was one), or a shape
    description; never payload bytes and never a bearer.
    """
    log.warning(
        json.dumps(
            {
                "event": "model_listing_failed",
                "endpoint": display_host(connection.base_url),
                "error": reason,
            }
        )
    )
    return reason


def _listing_from_payload(
    connection: LlmConnection, payload: object, now: Callable[[], float]
) -> ModelListing:
    """Sorted unique ids; a payload no id can be read from is an (also non-fatal) shape error.

    A payload that is not a list at all can only come from an injected
    ``list_models`` seam, and it fails soft as a SHAPE too: iterating a mapping or
    ``None`` would put a bare ``KeyError`` / ``TypeError`` in the dialog's caption,
    which E6 forbids for any payload whatever produced it.
    """
    if not isinstance(payload, list):
        raise UnexpectedListingPayloadError(
            _unexpected_payload_message(f"got {type(payload).__name__} payload")
        )
    entries = {entry["id"]: entry for entry in payload if _has_model_id(entry)}
    if entries or not payload:
        return ModelListing(tuple(sorted(entries)), None, now(), entries)
    reason = _unexpected_payload_message(_entries_detail(payload))
    return ModelListing((), _log_listing_failure(connection, reason), now())


def _has_model_id(entry: Any) -> bool:
    """An id the dialog can offer: a non-empty STRING.

    Not ``str(entry["id"])``: coercing an out-of-contract id would offer a model
    name the endpoint never advertised (``5`` → ``"5"``, ``True`` → ``"True"``),
    and the failure would land later, on the first chat call.
    """
    identifier = entry.get("id") if isinstance(entry, dict) else None
    return isinstance(identifier, str) and identifier != ""


def _unexpected_payload_message(detail: str) -> str:
    """Name the SHAPE the endpoint sent instead — never its bytes (H4), like E2's token errors."""
    return f"unexpected /models payload: {_EXPECTED_LISTING_SHAPE}, {detail}"


def _body_detail(page: Any) -> str:
    """The 200 body's top-level keys — declared ones it sent plus extras — and, when ``data``
    is there but is not a list, the type it came as.

    Not ``page.to_dict()``: serializing a page whose ``data`` is not a list of
    models emits a pydantic warning, and this path exists for exactly that body.
    """
    keys = sorted({*page.model_fields_set, *(page.model_extra or {})})
    if page.data is None:
        return f"got body keys={keys}"
    return f"got body keys={keys}, data of type {type(page.data).__name__}"


def _entries_detail(payload: ListingEntries) -> str:
    """Why no id could be read: an entry that is not an object names its type; one that carries
    no ``id`` key names its keys; an ``id`` that IS there says which way it is wrong — blank, or
    not a string. (``got keys=['id']`` would contradict itself.)"""
    first = payload[0]
    if not isinstance(first, dict):
        return f"got {type(first).__name__} entries"
    if "id" not in first:
        return f"got keys={sorted(first)}"
    if not isinstance(first["id"], str):
        return f"got ids of type {type(first['id']).__name__}"
    return "got entries whose ids are empty"


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
    "UnexpectedListingPayloadError",
    "cached_model_listing",
    "clear_model_listing_cache",
    "fetch_model_ids",
    "fetch_models_payload",
)
