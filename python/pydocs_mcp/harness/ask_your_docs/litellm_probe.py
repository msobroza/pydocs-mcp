"""The LiteLLM ``/model_group/info`` probe (model-params v2 §2 step 5, D10).

Split out of ``model_listing`` (line budget) and built on its client: the same
bearer and auth decision, 10 s timeout, one bounded retry, and a TTL cache
keyed by ``(base_url, auth identity)``. It runs only while detection steps 1-4
leave the profile undecided, and only from the Connection dialog — never at
page start, never in eval. It fails soft with one body-free log line.

Example (the undecided check alone; a host rule has already decided openrouter.ai):
    >>> from pydocs_mcp.harness.ask_your_docs.provider_profiles import wire_profile
    >>> wire_profile("auto", "https://openrouter.ai/api/v1").value
    'openrouter'
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BEARER_ERRORS,
    BearerSource,
    display_host,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import LlmConnection
from pydocs_mcp.harness.ask_your_docs.model_listing import (
    _MODEL_LISTING_TTL_SECONDS,
    _cache_key,
    _listing_client,
)
from pydocs_mcp.harness.ask_your_docs.provider_profiles import (
    ProviderProfile,
    display_profile,
    wire_profile,
)

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

GroupInfoSeam = Callable[[LlmConnection, BearerSource], Awaitable[Any]]
GroupRows = Mapping[str, Mapping[str, Any]]  # model_group → its /model_group/info row
_LITELLM_NOTE = "drop_params on the proxy can silently remove sent settings"
_group_info_cache: dict[tuple, tuple[float, GroupRows | None]] = {}  # None = not LiteLLM
_litellm_logged: set[str | None] = set()  # D10: one line per endpoint per process


class _NotGroupInfoError(ValueError):
    """A 200 whose body has no ``data[].model_group`` — not a LiteLLM proxy."""

    status_code = 200


async def litellm_group_row(
    connection: LlmConnection,
    bearer: BearerSource,
    listing_entry: Mapping[str, Any] | None,
    *,
    group_info: GroupInfoSeam | None = None,
    transport: Any = None,
    now: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any] | None:
    """The chosen model's LiteLLM row — ``{}`` = LiteLLM without one, None = not LiteLLM.

    Runs only while detection steps 1-4 leave the profile undecided, and only from the
    Connection dialog: never at page start, never in eval (their wire is host-only).
    """
    if not _profile_undecided(connection, listing_entry):
        return None
    rows = await cached_litellm_group_info(
        connection, bearer, group_info=group_info, transport=transport, now=now
    )
    return None if rows is None else rows.get(connection.model or "", {})


def _profile_undecided(connection: LlmConnection, entry: Mapping[str, Any] | None) -> bool:
    """Steps 1-4 of §2: nothing declared, no known host, no vLLM ``owned_by``."""
    if connection.provider != "auto":
        return False
    wire = wire_profile(connection.provider, connection.base_url)
    return display_profile(wire, entry, None).profile is ProviderProfile.GENERIC


async def cached_litellm_group_info(
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    group_info: GroupInfoSeam | None = None,
    transport: Any = None,
    now: Callable[[], float] = time.monotonic,
) -> GroupRows | None:
    """The listing's TTL cache, keyed the same ``(base_url, auth identity)``; failures cache too."""
    key = _cache_key(connection)
    cached = _group_info_cache.get(key)
    if cached is not None and now() - cached[0] < _MODEL_LISTING_TTL_SECONDS:
        return cached[1]
    rows = await fetch_litellm_group_info(
        connection, bearer, group_info=group_info, transport=transport
    )
    _group_info_cache[key] = (now(), rows)
    return rows


async def fetch_litellm_group_info(
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    group_info: GroupInfoSeam | None = None,
    transport: Any = None,
) -> GroupRows | None:
    """``GET <base_url minus /v1>/model_group/info`` on the listing's client; None = not LiteLLM.

    Fails soft with one body-free log line; a bearer failure propagates, as the listing's does (H3).
    """
    fetch = group_info or (lambda c, b: _group_info_payload(c, b, transport))
    try:
        rows = _group_rows(await fetch(connection, bearer))
    except BEARER_ERRORS:
        raise
    except Exception as exc:  # broad on purpose: the probe only refines what the dialog shows
        _log_litellm_probe_failed(connection, exc)
        return None
    _log_litellm_detected(connection)
    return rows


async def _group_info_payload(
    connection: LlmConnection, bearer: BearerSource, transport: Any
) -> Any:
    """The listing's client — same bearer and auth decision, 10 s timeout, one bounded retry."""
    import httpx  # heavy-ish; lazy by contract (cli.py must not import it)

    client = _listing_client(connection, bearer, transport)
    # An absolute URL: LiteLLM serves the route beside /v1, not under it.
    response = await client.get(_group_info_url(connection.base_url), cast_to=httpx.Response)
    return response.json()


def _group_info_url(base_url: str | None) -> str:
    return (base_url or "").rstrip("/").removesuffix("/v1") + "/model_group/info"


def _group_rows(payload: Any) -> dict[str, Mapping[str, Any]]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    rows = {
        row["model_group"]: row
        for row in (data if isinstance(data, list) else ())
        if isinstance(row, Mapping) and isinstance(row.get("model_group"), str)
    }
    if not rows:
        raise _NotGroupInfoError("expected {'data': [{'model_group': ...}]}")
    return rows


def _log_litellm_probe_failed(connection: LlmConnection, exc: Exception) -> None:
    """The status alone (or null for a transport failure) — never the key, never the body."""
    status = getattr(exc, "status_code", None)
    fields = {"endpoint": display_host(connection.base_url), "status": status}
    if not isinstance(status, int):
        fields["status"] = None
    log.info(json.dumps({"event": "litellm_probe_failed", **fields}))


def _log_litellm_detected(connection: LlmConnection) -> None:
    """D10: LiteLLM's ``drop_params`` is the operator's to know about — said once per endpoint."""
    if connection.base_url in _litellm_logged:
        return
    _litellm_logged.add(connection.base_url)
    endpoint = display_host(connection.base_url)
    log.info(json.dumps({"event": "litellm_detected", "endpoint": endpoint, "note": _LITELLM_NOTE}))


def clear_litellm_probe_cache(connection: LlmConnection | None = None) -> None:
    """Evict one connection's probe result, or everything and the D10 once-per-endpoint memory."""
    if connection is None:
        _group_info_cache.clear()
        _litellm_logged.clear()
        return
    _group_info_cache.pop(_cache_key(connection), None)


__all__ = (
    "GroupInfoSeam",
    "GroupRows",
    "cached_litellm_group_info",
    "clear_litellm_probe_cache",
    "fetch_litellm_group_info",
    "litellm_group_row",
)
