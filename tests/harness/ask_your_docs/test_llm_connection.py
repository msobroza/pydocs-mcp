"""LlmConnection resolution, identity and the bearer registry (LLM-connection
design §4.2–§4.3 — AC-1, AC-2, AC-20, AC-26 (identity half), AC-28, AC-31,
AC-39, AC-40). Core deps only."""

from __future__ import annotations

import json
import logging

import pytest

from pydocs_mcp.harness.ask_your_docs import bearer_tokens as bt
from pydocs_mcp.harness.ask_your_docs import llm_connection as lc
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    EnvironmentKeyBearer,
    NoBearer,
    TokenServiceBearer,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    LlmConnection,
    bearer_for_connection,
    clear_bearer_registry,
    connection_identity,
    resolve_llm_connection,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import (
    AuthMode,
    LlmConnectionConfig,
    VisionRule,
)

_YAML_URL = "https://llm.internal/v1"
_TOKEN_URL = "http://localhost:8899/access-token"
_NONE = ConnectionOverride()


def _block(**overrides) -> LlmConnectionConfig:
    data = {"base_url": _YAML_URL, "auth": {"token_url": _TOKEN_URL}}
    data.update(overrides)
    return LlmConnectionConfig.model_validate(data)


def _resolve(block, env=None, launch=_NONE, dialog=_NONE) -> LlmConnection:
    return resolve_llm_connection(block, env or {}, launch, dialog, config_path="cfg.yaml")


@pytest.fixture(autouse=True)
def _fresh_registry():
    clear_bearer_registry()
    yield
    clear_bearer_registry()


@pytest.mark.parametrize(
    ("env", "launch", "dialog", "expected", "tier"),
    [
        ({}, _NONE, _NONE, _YAML_URL, "yaml"),
        (
            {"OPENAI_BASE_URL": "http://localhost:8000/v1"},
            _NONE,
            _NONE,
            "http://localhost:8000/v1",
            "environment",
        ),
        (
            {"OPENAI_BASE_URL": "http://localhost:8000/v1"},
            ConnectionOverride(base_url="http://gpu-box:8000/v1"),
            _NONE,
            "http://gpu-box:8000/v1",
            "cli",
        ),
        (
            {"OPENAI_BASE_URL": "http://localhost:8000/v1"},
            ConnectionOverride(base_url="http://gpu-box:8000/v1"),
            ConnectionOverride(base_url="http://other/v1"),
            "http://other/v1",
            "dialog",
        ),
        ({"OPENAI_BASE_URL": ""}, _NONE, _NONE, _YAML_URL, "yaml"),  # empty = unset
    ],
)
def test_base_url_precedence_fold(caplog, env, launch, dialog, expected, tier) -> None:
    """AC-1: YAML < environment < CLI < dialog, field by field; the log names the tier."""
    caplog.set_level(logging.INFO)
    connection = _resolve(_block(), env, launch, dialog)
    assert connection.base_url == expected
    resolved = [
        json.loads(r.getMessage())
        for r in caplog.records
        if "connection_resolved" in r.getMessage()
    ]
    assert resolved and resolved[-1]["base_url_tier"] == tier


def test_model_precedence_and_the_two_bottoms() -> None:
    """AC-1: model follows the same fold; the bottom is _DEFAULT_MODEL without a block and
    None with one (D2: pick in the dialog)."""
    assert _resolve(None).model == "gpt-4o-mini"
    assert _resolve(_block()).model is None
    assert _resolve(_block(model="yaml-m")).model == "yaml-m"
    assert _resolve(_block(model="yaml-m"), {"LLM_MODEL": "env-m"}).model == "env-m"
    assert (
        _resolve(_block(), {"LLM_MODEL": "env-m"}, ConnectionOverride(model="cli-m")).model
        == "cli-m"
    )
    assert (
        _resolve(
            _block(), {}, ConnectionOverride(model="cli-m"), ConnectionOverride(model="dlg-m")
        ).model
        == "dlg-m"
    )
    assert _resolve(_block(), {"LLM_MODEL": ""}).model is None


def test_no_block_is_todays_shape() -> None:
    """AC-2: no block ⇒ lenient OPENAI_API_KEY bearer, DETECT, (401,), nothing to compare against."""
    connection = _resolve(None, {"OPENAI_BASE_URL": "http://gpu-box:8000/v1"})
    assert connection.block_present is False
    assert connection.auth_mode is AuthMode.ENV_KEY
    assert connection.api_key_env == "OPENAI_API_KEY" and connection.token_url is None
    assert connection.vision_rule is VisionRule.DETECT and connection.vision_model is None
    assert connection.renew_on_status == (401,)
    assert connection.configured_base_url is None
    assert connection.origin_changed is False and connection.cleartext_bearer is False
    bearer = bearer_for_connection(connection)
    assert isinstance(bearer, EnvironmentKeyBearer) and bearer.required is False
    assert connection.config_path == "cfg.yaml"


def test_block_without_auth_is_none_mode() -> None:
    """AC-2: block present, auth absent ⇒ NONE and the Null Object bearer."""
    connection = _resolve(LlmConnectionConfig(base_url=_YAML_URL))
    assert connection.auth_mode is AuthMode.NONE
    assert connection.token_url is None and connection.api_key_env is None
    assert isinstance(bearer_for_connection(connection), NoBearer)
    assert connection.configured_base_url == _YAML_URL


def test_auth_fields_follow_the_block() -> None:
    token = _resolve(_block(token_field="access_token", renew_on_status=[401, 403]))
    assert token.auth_mode is AuthMode.TOKEN_SERVICE
    assert token.token_url == _TOKEN_URL and token.token_field == "access_token"
    assert token.renew_on_status == (401, 403)
    key = _resolve(LlmConnectionConfig.model_validate({"auth": {"api_key_env": "LLM_KEY"}}))
    assert key.auth_mode is AuthMode.ENV_KEY and key.api_key_env == "LLM_KEY"
    assert key.base_url is None and key.configured_base_url is None
    bearer = bearer_for_connection(key)
    assert isinstance(bearer, EnvironmentKeyBearer) and bearer.required is True


def test_empty_block_strings_are_absent() -> None:
    """The config model's exactly-one auth check is truthiness-based, so `token_url: ""` reaches
    the fold beside an api_key_env; no consumer may fetch a bearer from an empty URL."""
    connection = _resolve(
        LlmConnectionConfig.model_validate(
            {
                "base_url": "",
                "model": "",
                "token_field": "",
                "auth": {"token_url": "", "api_key_env": "LLM_KEY"},
            }
        )
    )
    assert connection.auth_mode is AuthMode.ENV_KEY
    assert connection.token_url is None and connection.api_key_env == "LLM_KEY"
    assert connection.base_url is None and connection.configured_base_url is None
    assert connection.model is None and connection.token_field is None
    assert connection.origin_changed is False


def test_vision_rules(caplog) -> None:
    """AC-20 + the four VisionRule values."""
    caplog.set_level(logging.WARNING)
    assert _resolve(_block()).vision_rule is VisionRule.DETECT
    assert _resolve(_block(vision=True)).vision_rule is VisionRule.MULTIMODAL
    assert _resolve(_block(vision=False)).vision_rule is VisionRule.TEXT_ONLY
    separate = _resolve(_block(model="main-a", vision={"model": "vision-b"}))
    assert separate.vision_rule is VisionRule.SEPARATE_MODEL and separate.vision_model == "vision-b"
    same = _resolve(_block(model="main-a", vision={"model": "main-a"}))
    assert same.vision_rule is VisionRule.MULTIMODAL and same.vision_model is None
    warnings = [
        r.getMessage() for r in caplog.records if "vision_model_equals_main" in r.getMessage()
    ]
    assert len(warnings) == 1 and "'main-a' equals the main model" in warnings[0]


def test_origin_change_is_flagged_and_logged_not_withheld(caplog) -> None:
    """AC-31 (resolution half): another origin at any tier ⇒ origin_changed, one warning, the
    same auth mode (the bearer still follows the endpoint); another path on the same origin
    ⇒ nothing."""
    caplog.set_level(logging.WARNING)
    moved = _resolve(_block(), {"OPENAI_BASE_URL": "https://gpu-box:8443/v1"})
    assert moved.origin_changed is True and moved.auth_mode is AuthMode.TOKEN_SERVICE
    records = [
        json.loads(r.getMessage())
        for r in caplog.records
        if "bearer_origin_changed" in r.getMessage()
    ]
    assert len(records) == 1
    assert records[0]["configured_origin"] == "https://llm.internal/v1"
    assert records[0]["resolved_origin"] == "https://gpu-box:8443/v1"
    caplog.clear()
    same_origin = _resolve(_block(), {}, ConnectionOverride(base_url="https://llm.internal/other"))
    assert same_origin.origin_changed is False
    assert not [r for r in caplog.records if "bearer_origin_changed" in r.getMessage()]
    vendor_default_key = _resolve(
        LlmConnectionConfig.model_validate({"auth": {"api_key_env": "LLM_KEY"}}),
        {"OPENAI_BASE_URL": "https://gpu-box:8443/v1"},
    )
    assert vendor_default_key.origin_changed is False  # no YAML base_url to compare against


def test_cleartext_bearer_is_flagged_and_logged_never_an_error(caplog) -> None:
    """AC-39 (resolution half): plain http to a non-loopback host with a bearer ⇒ a warning;
    loopback and the no-block path ⇒ nothing."""
    caplog.set_level(logging.WARNING)
    plain = _resolve(_block(base_url="http://llm.internal/v1"))
    assert plain.cleartext_bearer is True
    assert len([r for r in caplog.records if "bearer_over_cleartext" in r.getMessage()]) == 1
    caplog.clear()
    for loopback in (
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
    ):
        assert _resolve(_block(base_url=loopback)).cleartext_bearer is False
    assert (
        _resolve(LlmConnectionConfig(base_url="http://llm.internal/v1")).cleartext_bearer is False
    )
    assert _resolve(None, {"OPENAI_BASE_URL": "http://gpu-box:8000/v1"}).cleartext_bearer is False
    assert not [r for r in caplog.records if "bearer_over_cleartext" in r.getMessage()]


def test_connection_identity_carries_no_secret() -> None:
    """AC-26 (identity half): (auth_mode, token_url or api_key_env or "", block_present)."""
    assert connection_identity(_resolve(_block())) == (AuthMode.TOKEN_SERVICE, _TOKEN_URL, True)
    assert connection_identity(_resolve(None)) == (AuthMode.ENV_KEY, "OPENAI_API_KEY", False)
    explicit = _resolve(
        LlmConnectionConfig.model_validate({"auth": {"api_key_env": "OPENAI_API_KEY"}})
    )
    assert connection_identity(explicit) == (AuthMode.ENV_KEY, "OPENAI_API_KEY", True)
    assert connection_identity(_resolve(LlmConnectionConfig(base_url=_YAML_URL))) == (
        AuthMode.NONE,
        "",
        True,
    )


def test_bearer_registry_shares_one_source_per_identity() -> None:
    """AC-40 (registry half): the same identity ⇒ the same TokenServiceBearer object; a
    different token_url ⇒ another; clear_bearer_registry() resets."""
    first = bearer_for_connection(_resolve(_block()))
    assert isinstance(first, TokenServiceBearer)
    assert (
        bearer_for_connection(_resolve(_block(), {"OPENAI_BASE_URL": "http://other/v1"})) is first
    )
    other = bearer_for_connection(_resolve(_block(auth={"token_url": "http://localhost:9000/t"})))
    assert other is not first
    clear_bearer_registry()
    assert bearer_for_connection(_resolve(_block())) is not first


@pytest.mark.xfail(strict=True, reason="Tasks 5-6")
def test_network_constants_are_finite_and_bounded() -> None:
    """AC-28: every network-bound helper has an explicit timeout and a bounded attempt count."""
    assert 0 < bt._TOKEN_FETCH_TIMEOUT_SECONDS < 60
    assert 0 < bt._MIN_RENEW_INTERVAL_SECONDS < 60
    assert 1 <= bt._TOKEN_FETCH_ATTEMPTS <= 3
    assert len(bt._TOKEN_FETCH_BACKOFF_SECONDS) == 2
    assert 0 < lc._TEST_CONNECTION_TIMEOUT_SECONDS < 120
    from pydocs_mcp.harness.ask_your_docs import model_listing as ml

    assert 0 < ml._LISTING_TIMEOUT_SECONDS < 120 and ml._LISTING_MAX_RETRIES <= 3
    assert 0 < ml._MODEL_LISTING_TTL_SECONDS < 3600
