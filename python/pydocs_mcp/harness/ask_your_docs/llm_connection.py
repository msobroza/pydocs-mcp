"""The chat model's connection: one value object, resolved once per session.

LLM-connection design §4.2 (the value object), §4.3 (precedence), §4.4 (the
bearer registry), §4.5 (the client factory) and §4.7 (capability resolution).
The connection is resolved by a pure fold over four tiers — YAML <
environment (``OPENAI_BASE_URL`` / ``LLM_MODEL``) < CLI (``--base-url`` /
``--model``) < the Connection dialog — for ``base_url`` and ``model`` only;
``auth``, ``token_field``, ``renew_on_status`` and ``vision`` come from the
YAML block (and its ``PYDOCS_ASK_YOUR_DOCS__LLM__*`` env overlay) alone.

Light by contract: ``langchain_openai`` and ``openai`` are imported
function-locally inside the factory.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerSource,
    EnvironmentKeyBearer,
    NoBearer,
    RenewOnStatusAuth,
    StripAuthorizationAuth,
    TokenServiceBearer,
    display_host,
    display_url,
    redact_bearer,
    translate_auth_errors,
)
from pydocs_mcp.harness.ask_your_docs.multimodal import (
    CapabilitySource,
    ModelCapabilities,
    detect_capabilities,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import (
    _DEFAULT_API_KEY_ENV,
    _DEFAULT_MODEL,
    _DEFAULT_RENEW_ON_STATUS,
    AuthMode,
    LlmConnectionConfig,
    MultimodalDetectionConfig,
    VisionRule,
)

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_TIER_NAMES = ("yaml", "environment", "cli", "dialog")


@dataclass(frozen=True, slots=True)
class ConnectionOverride:
    """One shape for the CLI tier and the dialog tier; ``None`` = tier unset."""

    base_url: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class LlmConnection:
    """Endpoint, model, auth mode and vision rule for one session (design §4.2)."""

    base_url: str | None  # None = the SDK's vendor default, as today
    model: str | None  # None = not chosen yet (block present, no tier set it)
    auth_mode: AuthMode
    token_url: str | None  # TOKEN_SERVICE only
    api_key_env: str | None  # ENV_KEY only
    token_field: str | None  # None = the whole body, stripped, is the token
    renew_on_status: tuple[int, ...]
    vision_rule: VisionRule
    vision_model: str | None  # SEPARATE_MODEL only
    config_path: str | None  # the pydocs YAML the block came from
    block_present: bool  # False = no ask_your_docs.llm block (byte identity)
    configured_base_url: str | None  # the YAML base_url, kept for the origin check (H1)

    @property
    def origin_changed(self) -> bool:
        """H1: a YAML base_url exists and the effective one has another origin."""
        if self.configured_base_url is None or self.base_url is None:
            return False
        return _origin(self.configured_base_url) != _origin(self.base_url)

    @property
    def cleartext_bearer(self) -> bool:
        """H2: a bearer this design introduces travels over plain http to a non-loopback host."""
        if not self.block_present or self.auth_mode is AuthMode.NONE or self.base_url is None:
            return False
        parts = urlsplit(self.base_url)
        return parts.scheme == "http" and (parts.hostname or "") not in _LOOPBACK_HOSTS


def _origin(url: str) -> tuple[str, str, int | None]:
    # scheme / hostname / port — never netloc, which carries userinfo (H4).
    parts = urlsplit(url)
    return (parts.scheme, parts.hostname or "", parts.port)


def resolve_llm_connection(
    yaml_block: LlmConnectionConfig | None,
    environment: Mapping[str, str],
    launch: ConnectionOverride,
    dialog: ConnectionOverride,
    *,
    config_path: str | None,
) -> LlmConnection:
    """The pure precedence fold of design §4.3 — no I/O, no Streamlit."""
    base_url, base_tier = _fold_base_url(yaml_block, environment, launch, dialog)
    model, model_tier = _fold_model(yaml_block, environment, launch, dialog)
    connection = _build_llm_connection(yaml_block, base_url, model, config_path=config_path)
    _log_resolution(connection, base_tier, model_tier)
    return connection


def _fold_base_url(
    block: LlmConnectionConfig | None,
    environment: Mapping[str, str],
    launch: ConnectionOverride,
    dialog: ConnectionOverride,
) -> tuple[str | None, str]:
    """YAML < ``OPENAI_BASE_URL`` < ``--base-url`` < the dialog."""
    return _fold_tiers(
        _yaml_field(block, "base_url"),
        environment.get("OPENAI_BASE_URL"),
        launch.base_url,
        dialog.base_url,
    )


def _fold_model(
    block: LlmConnectionConfig | None,
    environment: Mapping[str, str],
    launch: ConnectionOverride,
    dialog: ConnectionOverride,
) -> tuple[str | None, str]:
    """The same fold; without a block the bottom is today's page default (byte identity)."""
    model, tier = _fold_tiers(
        _yaml_field(block, "model"),
        environment.get("LLM_MODEL"),
        launch.model,
        dialog.model,
    )
    if model is None and block is None:
        return _DEFAULT_MODEL, "default"
    return model, tier  # None with a block = pick in the dialog (D2)


def _build_llm_connection(
    block: LlmConnectionConfig | None,
    base_url: str | None,
    model: str | None,
    *,
    config_path: str | None,
) -> LlmConnection:
    """The folded endpoint and model, plus the fields only the YAML block owns."""
    auth_mode, token_url, api_key_env = _auth_fields(block)
    vision_rule, vision_model = _vision_fields(block, model)
    return LlmConnection(
        base_url=base_url,
        model=model,
        auth_mode=auth_mode,
        token_url=token_url,
        api_key_env=api_key_env,
        token_field=_yaml_field(block, "token_field"),
        renew_on_status=_renew_on_status(block),
        vision_rule=vision_rule,
        vision_model=vision_model,
        config_path=config_path,
        block_present=block is not None,
        configured_base_url=_yaml_field(block, "base_url"),
    )


def _renew_on_status(block: LlmConnectionConfig | None) -> tuple[int, ...]:
    """The block's renewable statuses; without a block, the design default (401 alone)."""
    return tuple(block.renew_on_status) if block is not None else _DEFAULT_RENEW_ON_STATUS


def _yaml_field(block: LlmConnectionConfig | None, field: str) -> str | None:
    return _absent_if_empty(getattr(block, field)) if block is not None else None


def _absent_if_empty(value: str | None) -> str | None:
    """``""`` means unset everywhere in this fold.

    The config model's exactly-one auth check is truthiness-based, so
    ``token_url: ""`` validates beside an ``api_key_env`` and would otherwise
    reach the factory, which asks ``token_url is not None`` before fetching.
    """
    return value or None


def _fold_tiers(*values: str | None) -> tuple[str | None, str]:
    """Lowest tier first; an empty string means "unset at that tier" (today's `or None`)."""
    chosen, tier = None, "none"
    for name, value in zip(_TIER_NAMES, values, strict=True):
        if value:
            chosen, tier = value, name
    return chosen, tier


def _auth_fields(block: LlmConnectionConfig | None) -> tuple[AuthMode, str | None, str | None]:
    if block is None:
        return AuthMode.ENV_KEY, None, _DEFAULT_API_KEY_ENV  # the lenient no-block bearer
    if block.auth is None:
        return AuthMode.NONE, None, None
    token_url = _absent_if_empty(block.auth.token_url)
    if token_url is not None:
        return AuthMode.TOKEN_SERVICE, token_url, None
    return AuthMode.ENV_KEY, None, _absent_if_empty(block.auth.api_key_env)


def _vision_fields(
    block: LlmConnectionConfig | None, model: str | None
) -> tuple[VisionRule, str | None]:
    """``vision``: null = detect, true / false = the main model, a mapping = a second model."""
    vision = block.vision if block is not None else None
    if vision is None:
        return VisionRule.DETECT, None
    if isinstance(vision, bool):
        return (VisionRule.MULTIMODAL if vision else VisionRule.TEXT_ONLY), None
    if vision.model == model:  # design E9: the same model is not a separate model
        _log_vision_model_equals_main(model)
        return VisionRule.MULTIMODAL, None
    return VisionRule.SEPARATE_MODEL, vision.model


def _log_resolution(connection: LlmConnection, base_tier: str, model_tier: str) -> None:
    """One INFO line naming the winning tiers; H1 and H2 ride beside it as warnings."""
    _log_connection_resolved(connection, base_tier, model_tier)
    if connection.origin_changed:  # H1: visible, never withheld
        _log_bearer_origin_changed(connection)
    if connection.cleartext_bearer:  # H2: visible, never an error
        _log_bearer_over_cleartext(connection)


def _log_connection_resolved(connection: LlmConnection, base_tier: str, model_tier: str) -> None:
    log.info(
        json.dumps(
            {
                "event": "connection_resolved",
                "base_url_tier": base_tier,
                "model_tier": model_tier,
                "endpoint": display_host(connection.base_url),
                "auth_mode": connection.auth_mode.value,
                "vision_rule": connection.vision_rule.value,
            }
        )
    )


def _log_bearer_origin_changed(connection: LlmConnection) -> None:
    log.warning(
        json.dumps(
            {
                "event": "bearer_origin_changed",
                "configured_origin": display_url(connection.configured_base_url or ""),
                "resolved_origin": display_url(connection.base_url or ""),
            }
        )
    )


def _log_bearer_over_cleartext(connection: LlmConnection) -> None:
    log.warning(
        json.dumps(
            {"event": "bearer_over_cleartext", "endpoint": display_url(connection.base_url or "")}
        )
    )


def _log_vision_model_equals_main(model: str | None) -> None:
    log.warning(
        json.dumps(
            {
                "event": "vision_model_equals_main",
                "model": model,
                "message": (
                    f"ask_your_docs.llm.vision.model {model!r} equals the main model; "
                    "treating as vision: true"
                ),
            }
        )
    )


def connection_identity(connection: LlmConnection) -> tuple[AuthMode, str, bool]:
    """Where the bearer comes from, without carrying it — the cache and registry key (§1.4)."""
    return (
        connection.auth_mode,
        connection.token_url or connection.api_key_env or "",
        connection.block_present,
    )


# One bearer per identity per process: the app and the eval binding get the
# same object for the same identity, so a 1300-record campaign fetches one
# token, not one per sample (design §4.4).
_bearer_registry: dict[tuple[AuthMode, str, bool], BearerSource] = {}
_registry_lock = threading.Lock()


def bearer_for_connection(connection: LlmConnection) -> BearerSource:
    """The registry's bearer for the connection's identity (``NoBearer`` for ``NONE``)."""
    if connection.auth_mode is AuthMode.NONE:
        return NoBearer()
    identity = connection_identity(connection)
    with _registry_lock:
        bearer = _bearer_registry.get(identity)
        if bearer is None:
            bearer = _bearer_registry[identity] = _new_bearer(connection)
    return bearer


def _new_bearer(connection: LlmConnection) -> BearerSource:
    if connection.auth_mode is AuthMode.TOKEN_SERVICE:
        return TokenServiceBearer(connection.token_url or "", token_field=connection.token_field)
    # Strict for an explicit auth.api_key_env; lenient (no header, no error) on the no-block path.
    return EnvironmentKeyBearer(
        connection.api_key_env or _DEFAULT_API_KEY_ENV, required=connection.block_present
    )


def clear_bearer_registry() -> None:
    """Test seam: forget every bearer (the sibling of ``clear_detection_cache``)."""
    with _registry_lock:
        _bearer_registry.clear()


# WHY a placeholder: an empty api_key is SDK-version-fragile (a newer release
# rejects it at construction); the header is stripped on the wire instead.
_NO_AUTH_PLACEHOLDER = "no-auth"
_TEST_CONNECTION_TIMEOUT_SECONDS = 15.0
_TEST_CONNECTION_PROMPT = "Reply with the single word OK."
_TEST_REPLY_MAX_CHARS = 40


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
    from openai import DefaultAsyncHttpxClient  # heavy; lazy by contract

    extra = {"transport": transport} if transport is not None else {}
    return DefaultAsyncHttpxClient(auth=auth, **extra)


def httpx_clients(auth: Any, transport: Any) -> dict[str, Any]:
    """Both clients: ``ainvoke`` uses the async pair, ``invoke`` the sync pair (design §4.5 rule 4)."""
    return {
        "http_client": sync_httpx_client(auth, transport),
        "http_async_client": async_httpx_client(auth, transport),
    }


def build_chat_model(
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    model: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    tolerate_missing_key: bool = False,
    transport: Any = None,
) -> Any:
    """The one ``ChatOpenAI`` construction site (design §4.5).

    With no ``ask_your_docs.llm`` block this is exactly today's call —
    ``ChatOpenAI(model=..., base_url=...)`` plus the caller's own ``timeout``
    / ``max_retries`` — pinned by a kwargs spy (AC-19). ``transport`` is a
    test seam: when given, both httpx clients are built and carry it.
    """
    from langchain_openai import ChatOpenAI  # heavy; lazy by contract

    kwargs: dict[str, Any] = {"model": model or connection.model, "base_url": connection.base_url}
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    api_key, auth = connection_auth_kwargs(
        connection, bearer, tolerate_missing_key=tolerate_missing_key
    )
    if api_key is not None:
        kwargs["api_key"] = api_key
    if auth is not None or transport is not None:
        kwargs.update(httpx_clients(auth, transport))
    return ChatOpenAI(**kwargs)


async def run_connection_test(
    connection: LlmConnection, bearer: BearerSource, *, transport: Any = None
) -> str:
    """One round-trip on a candidate connection (design §4.9 item 5, E11) — always a caption.

    AC-43 is "always a caption, never a raise", so the CONSTRUCTION is inside the
    boundary too: a connection with no model chosen yet, or the no-block path with
    OPENAI_API_KEY unset, fails in ``ChatOpenAI.__init__`` before any request, and the
    dialog must show that as the same redacted caption a request failure gets.
    """
    try:
        llm = build_chat_model(
            connection,
            bearer,
            timeout_seconds=_TEST_CONNECTION_TIMEOUT_SECONDS,
            max_retries=0,
            transport=transport,
        )
        with translate_auth_errors(bearer):
            reply = await llm.ainvoke(_TEST_CONNECTION_PROMPT)
    except Exception as exc:  # broad on purpose: every failure becomes the caption, redacted (H4)
        return f"test failed: {exc.__class__.__name__}: {redact_bearer(str(exc), bearer)}"
    return f"test passed: {str(reply.content).strip()[:_TEST_REPLY_MAX_CHARS]}"


# The non-DETECT rules, answered without probing. Under SEPARATE_MODEL the main model is NOT the
# image model: the MAIN-route readers (effective_tools, the E13 gate, text_only_policy) go blind.
_CONFIGURED_SEES = ModelCapabilities(multimodal=True, source=CapabilitySource.CONFIGURED)
_CONFIGURED_BLIND = ModelCapabilities(multimodal=False, source=CapabilitySource.CONFIGURED)
_CONFIGURED_VERDICTS: dict[VisionRule, tuple[ModelCapabilities, ModelCapabilities]] = {
    VisionRule.MULTIMODAL: (_CONFIGURED_SEES, _CONFIGURED_SEES),
    VisionRule.TEXT_ONLY: (_CONFIGURED_BLIND, _CONFIGURED_BLIND),
    VisionRule.SEPARATE_MODEL: (_CONFIGURED_BLIND, _CONFIGURED_SEES),
}


async def resolve_vision_capabilities(
    connection: LlmConnection, bearer: BearerSource, detection: MultimodalDetectionConfig
) -> tuple[ModelCapabilities, ModelCapabilities]:
    """The single resolver of design §4.7: the ``(main, vision)`` verdicts — every
    configured rule reads the table above, ``DETECT`` runs the ladder authenticated."""
    if connection.vision_rule is not VisionRule.DETECT:
        return _CONFIGURED_VERDICTS[connection.vision_rule]
    main = await detect_capabilities(
        connection.model or "", connection.base_url, detection, connection=connection, bearer=bearer
    )
    return main, main


__all__ = (
    "AuthMode",
    "ConnectionOverride",
    "LlmConnection",
    "VisionRule",
    "async_httpx_client",
    "bearer_for_connection",
    "build_chat_model",
    "clear_bearer_registry",
    "connection_auth_kwargs",
    "connection_identity",
    "httpx_clients",
    "resolve_llm_connection",
    "resolve_vision_capabilities",
    "run_connection_test",
    "sync_httpx_client",
)
