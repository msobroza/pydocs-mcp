"""The ONE auth decision and the SDK's httpx clients (LLM-connection design §4.5).

Moved out of ``llm_connection`` (line budget) when ``build_chat_model`` gained
the ``wire`` seam; ``llm_connection`` re-exports every name here, so import
paths are unchanged. Light by contract: ``openai`` loads function-locally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerSource,
    RenewOnStatusAuth,
    StripAuthorizationAuth,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode

if TYPE_CHECKING:
    from pydocs_mcp.harness.ask_your_docs.llm_connection import LlmConnection

# WHY a placeholder: an empty api_key is SDK-version-fragile (a newer release
# rejects it at construction); the header is stripped on the wire instead.
_NO_AUTH_PLACEHOLDER = "no-auth"


def connection_auth_kwargs(
    connection: LlmConnection, bearer: BearerSource, *, tolerate_missing_key: bool = False
) -> tuple[Any, Any]:
    """The ONE auth decision (design §4.5): ``(api_key, httpx auth)``.

    ``api_key`` ``None`` = rule 1 (no block: the SDK reads OPENAI_API_KEY
    itself); a sync callable = rules 2 and 4 (re-read before every attempt);
    the placeholder = rule 3 (the header is stripped on the wire).
    ``tolerate_missing_key`` is the rule-1 carve-out for the listing and rung
    3, which must work with the variable unset, as today's bare GET does.
    """
    if not connection.block_present:
        if not tolerate_missing_key:
            return None, None
        # WHY eager, when every other branch hands over the callable: the carve-out has to
        # know NOW whether a key exists at all, because "no key" means a request with no
        # Authorization header — placeholder + strip — a shape a lazy callable cannot pick.
        if bearer.current():
            return bearer.current, None
        return _NO_AUTH_PLACEHOLDER, StripAuthorizationAuth()
    if connection.auth_mode is AuthMode.ENV_KEY:
        return bearer.current, None
    if connection.auth_mode is AuthMode.NONE:
        return _NO_AUTH_PLACEHOLDER, StripAuthorizationAuth()
    # E4: the renewing flow is the token service's ALONE, named explicitly — a future
    # AuthMode member must go red here, never inherit the most privileged flow by falling through.
    if connection.auth_mode is AuthMode.TOKEN_SERVICE:
        return bearer.current, RenewOnStatusAuth(bearer, connection.renew_on_status)
    raise ValueError(
        f"unhandled auth mode: got {connection.auth_mode!r}, expected one of "
        f"{AuthMode.NONE!r}, {AuthMode.ENV_KEY!r}, {AuthMode.TOKEN_SERVICE!r}"
    )


def sync_httpx_client(auth: Any, transport: Any) -> Any:
    """The SDK's own sync client (its timeout and limits), carrying ``auth`` and a test transport."""
    from openai import DefaultHttpxClient  # heavy; lazy by contract

    extra = {"transport": transport} if transport is not None else {}
    return DefaultHttpxClient(auth=auth, **extra)


def async_httpx_client(auth: Any, transport: Any) -> Any:
    """The async twin of :func:`sync_httpx_client` — ``ainvoke``'s client, and the listing's."""
    from openai import DefaultAsyncHttpxClient  # heavy; lazy by contract

    extra = {"transport": transport} if transport is not None else {}
    return DefaultAsyncHttpxClient(auth=auth, **extra)


def httpx_clients(auth: Any, transport: Any) -> dict[str, Any]:
    """Both clients: ``ainvoke`` uses the async pair, ``invoke`` the sync pair (design §4.5 rule 4)."""
    return {
        "http_client": sync_httpx_client(auth, transport),
        "http_async_client": async_httpx_client(auth, transport),
    }


__all__ = (
    "async_httpx_client",
    "connection_auth_kwargs",
    "httpx_clients",
    "sync_httpx_client",
)
