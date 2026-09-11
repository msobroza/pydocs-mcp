"""The chat model's connection: one value object, resolved once per session.

LLM-connection design §4.2 (the value object), §4.3 (precedence), §4.4 (the
bearer registry), §4.5 (the client factory) and §4.7 (capability resolution).
The connection is resolved by a pure fold over four tiers — YAML <
environment (``OPENAI_BASE_URL`` / ``LLM_MODEL``) < CLI (``--base-url`` /
``--model``) < the Connection dialog — for ``base_url`` and ``model`` only;
``auth``, ``token_field``, ``renew_on_status``, ``vision`` and ``provider`` come
from the YAML block (and its ``PYDOCS_ASK_YOUR_DOCS__LLM__*`` env overlay) alone;
``params`` has two tiers — that block < the dialog's COMPLETE snapshot, which
replaces it whole (model-params v2 §4). The launcher carries no params.

Light by contract: ``langchain_openai`` and ``openai`` are imported
function-locally inside the factory.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    NO_BEARER,
    BearerSource,
    EnvironmentKeyBearer,
    TokenServiceBearer,
    display_host,
    display_url,
)
from pydocs_mcp.harness.ask_your_docs.chat_wire import NO_WIRE_PARAMS, WireParams
from pydocs_mcp.harness.ask_your_docs.connection_auth import (
    async_httpx_client,
    connection_auth_kwargs,
    httpx_clients,
    sync_httpx_client,
)
from pydocs_mcp.harness.ask_your_docs.connection_test import run_connection_test
from pydocs_mcp.harness.ask_your_docs.multimodal import (
    CapabilitySource,
    ModelCapabilities,
    detect_capabilities,
)
from pydocs_mcp.harness.ask_your_docs.reasoning_capture import reasoning_chat_model_class
from pydocs_mcp.retrieval.config.ask_your_docs_models import (
    _DEFAULT_API_KEY_ENV,
    _DEFAULT_MODEL,
    _DEFAULT_RENEW_ON_STATUS,
    AuthMode,
    LlmConnectionConfig,
    MultimodalDetectionConfig,
    VisionRule,
)
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import (
    _DEFAULT_PROVIDER,
    ChatParamsConfig,
    ProviderName,
)

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# A spelled-out default port is the SAME origin as none at all (RFC 3986 §3.2.3), so
# https://host and https://host:443 must not read as a bearer-origin change (H1).
_DEFAULT_PORTS = {"http": 80, "https": 443}
_TIER_NAMES = ("yaml", "environment", "cli", "dialog")
_NO_CHAT_PARAMS = ChatParamsConfig()  # frozen, so one shared "send nothing" value


@dataclass(frozen=True, slots=True)
class ConnectionOverride:
    """One shape for the CLI tier and the dialog tier; ``None`` = tier unset."""

    base_url: str | None = None
    model: str | None = None
    # The dialog's complete snapshot (v2 §4); the fold ignores it on the launch tier.
    params: ChatParamsConfig | None = None


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
    provider: ProviderName = _DEFAULT_PROVIDER  # the declared profile; auto = by host
    params: ChatParamsConfig = _NO_CHAT_PARAMS  # what the chat model is asked for

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
    return (parts.scheme, parts.hostname or "", parts.port or _DEFAULT_PORTS.get(parts.scheme))


def resolve_llm_connection(
    yaml_block: LlmConnectionConfig | None,
    environment: Mapping[str, str],
    launch: ConnectionOverride,
    dialog: ConnectionOverride,
    *,
    config_path: str | None,
) -> LlmConnection:
    """The pure precedence fold of design §4.3 — no I/O, no Streamlit.

    Both fields ride the SAME four tiers (``_fold_tiers``); only the environment
    variable differs. ``model`` alone has a fifth rule at the bottom: with no block
    at all it falls back to today's page default, so byte identity holds.
    """
    base_url, base_tier = _fold_tiers(  # YAML < OPENAI_BASE_URL < --base-url < the dialog
        _yaml_field(yaml_block, "base_url"),
        environment.get("OPENAI_BASE_URL"),
        launch.base_url,
        dialog.base_url,
    )
    model, model_tier = _fold_tiers(  # the same fold, on LLM_MODEL / --model
        _yaml_field(yaml_block, "model"),
        environment.get("LLM_MODEL"),
        launch.model,
        dialog.model,
    )
    if model is None and yaml_block is None:  # None WITH a block = pick in the dialog (D2)
        model, model_tier = _DEFAULT_MODEL, "default"
    params, params_tier = _fold_params(yaml_block, dialog)
    connection = _build_llm_connection(yaml_block, base_url, model, params, config_path=config_path)
    _log_resolution(connection, base_tier, model_tier, params_tier)
    return connection


def _build_llm_connection(
    block: LlmConnectionConfig | None,
    base_url: str | None,
    model: str | None,
    params: ChatParamsConfig,
    *,
    config_path: str | None,
) -> LlmConnection:
    """The folded endpoint, model and params, plus the fields only the YAML block owns."""
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
        provider=block.provider if block is not None else _DEFAULT_PROVIDER,
        params=params,
    )


def _fold_params(
    block: LlmConnectionConfig | None, dialog: ConnectionOverride
) -> tuple[ChatParamsConfig, str]:
    """The dialog snapshot wins WHOLE — a key it leaves blank is not inherited from YAML."""
    if dialog.params is not None:
        return dialog.params, "dialog"
    if block is not None:
        return block.params, "yaml"  # the env overlay rides the block
    return _NO_CHAT_PARAMS, "none"


def _renew_on_status(block: LlmConnectionConfig | None) -> tuple[int, ...]:
    """The block's renewable statuses; without a block, ``_DEFAULT_RENEW_ON_STATUS``."""
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


def _log_json(emit: Callable[[str], None], event: str, **fields: object) -> None:
    """One structured line per event; ``emit`` picks the level (``log.info`` / ``log.warning``).

    The single ``json.dumps`` site in this module — CLAUDE.md §Logging asks for JSON
    with named fields, and four hand-rolled copies had four chances to drift.
    """
    emit(json.dumps({"event": event, **fields}))


def _log_resolution(
    connection: LlmConnection, base_tier: str, model_tier: str, params_tier: str
) -> None:
    """One INFO line naming the winning tiers; H1 and H2 ride beside it as warnings."""
    _log_json(
        log.info,
        "connection_resolved",
        base_url_tier=base_tier,
        model_tier=model_tier,
        endpoint=display_host(connection.base_url),
        auth_mode=connection.auth_mode.value,
        vision_rule=connection.vision_rule.value,
        provider=connection.provider,
        params_tier=params_tier,
    )
    if connection.origin_changed:  # H1: visible, never withheld
        _log_bearer_origin_changed(connection)
    if connection.cleartext_bearer:  # H2: visible, never an error
        _log_json(
            log.warning, "bearer_over_cleartext", endpoint=display_url(connection.base_url or "")
        )


def _log_bearer_origin_changed(connection: LlmConnection) -> None:
    _log_json(
        log.warning,
        "bearer_origin_changed",
        configured_origin=display_url(connection.configured_base_url or ""),
        resolved_origin=display_url(connection.base_url or ""),
    )


def _log_vision_model_equals_main(model: str | None) -> None:
    _log_json(
        log.warning,
        "vision_model_equals_main",
        model=model,
        message=(
            f"ask_your_docs.llm.vision.model {model!r} equals the main model; "
            "treating as vision: true"
        ),
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
        return NO_BEARER
    identity = connection_identity(connection)
    with _registry_lock:
        bearer = _bearer_registry.get(identity)
        if bearer is None:
            bearer = _bearer_registry[identity] = _new_bearer(connection)
    return bearer


def _new_bearer(connection: LlmConnection) -> BearerSource:
    """``_auth_fields`` guarantees the field each mode needs; a missing one is a wiring bug.

    Loud rather than defaulted: a ``TokenServiceBearer("")`` would report the token
    service as unreachable, and an ``api_key_env`` silently falling back to the SDK's
    own variable would read a DIFFERENT credential than the registry keyed the entry on.
    """
    if connection.auth_mode is AuthMode.TOKEN_SERVICE and connection.token_url:
        return TokenServiceBearer(connection.token_url, token_field=connection.token_field)
    # Strict for an explicit auth.api_key_env; lenient (no header, no error) on the no-block path.
    if connection.auth_mode is AuthMode.ENV_KEY and connection.api_key_env:
        return EnvironmentKeyBearer(connection.api_key_env, required=connection.block_present)
    raise ValueError(
        f"auth mode {connection.auth_mode.value!r} names no credential source: got "
        f"token_url={connection.token_url!r}, api_key_env={connection.api_key_env!r}, "
        "expected exactly one non-empty string"
    )


def clear_bearer_registry() -> None:
    """Test seam: forget every bearer (the sibling of ``clear_detection_cache``)."""
    with _registry_lock:
        _bearer_registry.clear()


def build_chat_model(
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    model: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    tolerate_missing_key: bool = False,
    transport: Any = None,
    capture_reasoning: bool = True,
    wire: WireParams = NO_WIRE_PARAMS,
) -> Any:
    """The one ``ChatOpenAI`` construction site (design §4.5).

    With no ``ask_your_docs.llm`` block this is exactly today's call —
    ``ChatOpenAI(model=..., base_url=...)`` plus the caller's own ``timeout``
    / ``max_retries`` — pinned by a kwargs spy (AC-19); the class keeps provider
    reasoning (``reasoning_capture``) unless ``capture_reasoning`` is False, the kwargs are
    unchanged. ``transport`` is a test seam: both httpx clients are then built with it.
    ``wire`` (model-params v2 §7) adds the resolved settings as first-class fields; only
    the main model and the Test connection pass one — the vision model, the image
    probe and the listings never do.
    """
    from langchain_openai import ChatOpenAI  # heavy; lazy by contract

    kwargs: dict[str, Any] = {"model": model or connection.model, "base_url": connection.base_url}
    kwargs.update(wire.chat_model_kwargs())
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
    chat_class = reasoning_chat_model_class(ChatOpenAI) if capture_reasoning else ChatOpenAI
    return chat_class(**kwargs)


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
