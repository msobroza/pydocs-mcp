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
from urllib.parse import urlsplit

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerSource,
    EnvironmentKeyBearer,
    NoBearer,
    TokenServiceBearer,
    display_host,
    display_url,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import (
    _DEFAULT_API_KEY_ENV,
    _DEFAULT_MODEL,
    _DEFAULT_RENEW_ON_STATUS,
    AuthMode,
    LlmConnectionConfig,
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


__all__ = (
    "AuthMode",
    "ConnectionOverride",
    "LlmConnection",
    "VisionRule",
    "bearer_for_connection",
    "clear_bearer_registry",
    "connection_identity",
    "resolve_llm_connection",
)
