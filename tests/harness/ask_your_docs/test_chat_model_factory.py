"""build_chat_model + translate_auth_errors on the locked SDK (LLM-connection
design §4.4–§4.5 — AC-3, AC-5–AC-9, AC-19, AC-31, AC-33, AC-35, AC-40, AC-43)."""

from __future__ import annotations

import asyncio
import inspect
import logging

import pytest

pytest.importorskip("langchain_openai")

import httpx
import langchain_openai
import openai._base_client as sdk_base

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerRejectedError,
    EnvironmentKeyBearer,
    NoBearer,
    RenewOnStatusAuth,
    StripAuthorizationAuth,
    TokenServiceBearer,
    translate_auth_errors,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    bearer_for_connection,
    build_chat_model,
    clear_bearer_registry,
    connection_auth_kwargs,
    resolve_llm_connection,
    run_connection_test,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

from ._connection_fakes import FakeTokenService, RecordingTransport, RotatingBearer

_URL = "http://llm.test/v1"
_TOKEN_URL = "http://localhost:8899/access-token"


@pytest.fixture(autouse=True)
def _fast_sdk_and_fresh_registry(monkeypatch):
    # The SDK sleeps 0.5 s+ between its own retries; the constants are module globals.
    monkeypatch.setattr(sdk_base, "INITIAL_RETRY_DELAY", 0.0)
    monkeypatch.setattr(sdk_base, "MAX_RETRY_DELAY", 0.0)
    clear_bearer_registry()
    yield
    clear_bearer_registry()


def _connection(block: dict | None, env: dict | None = None):
    cfg = LlmConnectionConfig.model_validate(block) if block is not None else None
    return resolve_llm_connection(
        cfg, env or {}, ConnectionOverride(), ConnectionOverride(), config_path=None
    )


def _token_service_setup(tokens: list[str]):
    service = FakeTokenService(tokens)
    connection = _connection({"base_url": _URL, "model": "m", "auth": {"token_url": _TOKEN_URL}})
    bearer = TokenServiceBearer(_TOKEN_URL, transport=service.transport, sleep=lambda _s: None)
    return service, connection, bearer


def _ask(llm, bearer, text: str = "hi") -> str:
    async def go() -> str:
        with translate_auth_errors(bearer):
            reply = await llm.ainvoke(text)
        return str(reply.content)

    return asyncio.run(go())


def test_no_block_is_exactly_todays_call(monkeypatch) -> None:
    """AC-19 / R2 byte identity: no block ⇒ ChatOpenAI(model=, base_url=) and nothing else;
    the probe's timeout / max_retries are added only when the caller passes them."""
    seen: list[dict] = []

    class _SpyChatOpenAI:
        def __init__(self, **kwargs) -> None:
            seen.append(kwargs)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _SpyChatOpenAI)
    connection = _connection(None, {"OPENAI_BASE_URL": _URL, "LLM_MODEL": "m"})
    build_chat_model(connection, bearer_for_connection(connection))
    assert seen == [{"model": "m", "base_url": _URL}]
    build_chat_model(connection, NoBearer(), model="probe-m", timeout_seconds=5.0, max_retries=0)
    assert seen[1] == {"model": "probe-m", "base_url": _URL, "timeout": 5.0, "max_retries": 0}


def test_capability_probe_call_shape_is_supported() -> None:
    """The production rung-4 seam (multimodal._default_probe_llm) calls the factory with
    exactly these keywords; the signature must keep accepting that call unchanged."""
    probe_kwargs = {"model": "probe-m", "timeout_seconds": 2.0, "max_retries": 0}
    signature = inspect.signature(build_chat_model)
    assert list(signature.parameters)[:2] == ["connection", "bearer"]
    bound = signature.bind(object(), object(), **probe_kwargs)
    assert len(bound.args) == 2 and set(bound.kwargs) == set(probe_kwargs)
    recorder = RecordingTransport([200])
    connection = _connection({"base_url": _URL, "model": "m"})
    llm = build_chat_model(connection, NoBearer(), transport=recorder.transport, **probe_kwargs)
    assert _ask(llm, NoBearer()) == "OK"
    assert llm.model_name == "probe-m"


def test_only_a_token_service_gets_the_renewing_auth(monkeypatch) -> None:
    """E4: the renewing flow is installed for a token service ALONE — an env-key bearer that
    became unavailable at renew time would raise BearerUnavailableError out of httpx's send."""
    monkeypatch.setenv("LLM_KEY", "key-one-1111")
    env_key = _connection({"base_url": _URL, "model": "m", "auth": {"api_key_env": "LLM_KEY"}})
    api_key, auth = connection_auth_kwargs(env_key, bearer_for_connection(env_key))
    assert callable(api_key) and auth is None
    no_auth = _connection({"base_url": _URL, "model": "m"})
    api_key, auth = connection_auth_kwargs(no_auth, NoBearer())
    assert api_key == "no-auth" and isinstance(auth, StripAuthorizationAuth)
    service, token_service, bearer = _token_service_setup(["tok-one-abcd"])
    api_key, auth = connection_auth_kwargs(token_service, bearer)
    assert api_key == bearer.current and isinstance(auth, RenewOnStatusAuth)
    assert auth.statuses == token_service.renew_on_status
    assert service.calls == 0  # the decision itself never fetches a token


def test_no_block_auth_is_the_sdks_own_rule_unless_tolerated(monkeypatch) -> None:
    """Rule 1 and its carve-out: the SDK reads OPENAI_API_KEY itself; the listing and rung 3
    pass tolerate_missing_key and must work with the variable unset (today's bare GET)."""
    connection = _connection(None, {"OPENAI_BASE_URL": _URL})
    bearer = bearer_for_connection(connection)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert connection_auth_kwargs(connection, bearer) == (None, None)
    api_key, auth = connection_auth_kwargs(connection, bearer, tolerate_missing_key=True)
    assert api_key == "no-auth" and isinstance(auth, StripAuthorizationAuth)
    monkeypatch.setenv("OPENAI_API_KEY", "key-one-1111")
    api_key, auth = connection_auth_kwargs(connection, bearer, tolerate_missing_key=True)
    assert api_key == bearer.current and auth is None


def test_none_mode_strips_the_header_on_the_wire() -> None:
    """AC-3: block without auth ⇒ the placeholder key never reaches the wire."""
    recorder = RecordingTransport([200])
    connection = _connection({"base_url": _URL, "model": "m"})
    llm = build_chat_model(connection, NoBearer(), transport=recorder.transport)
    assert _ask(llm, NoBearer()) == "OK"
    assert recorder.authorizations() == [None]


def test_env_key_callable_is_reevaluated_per_attempt(monkeypatch) -> None:
    """AC-5: with max_retries=2 and 500, 500, 200 the three attempts carry k1, k2, k3."""
    recorder = RecordingTransport([500, 500, 200])
    connection = _connection({"base_url": _URL, "model": "m", "auth": {"api_key_env": "LLM_KEY"}})
    bearer = RotatingBearer()
    llm = build_chat_model(connection, bearer, max_retries=2, transport=recorder.transport)
    assert _ask(llm, bearer) == "OK"
    assert recorder.authorizations() == ["Bearer k1", "Bearer k2", "Bearer k3"]
    assert recorder.retry_counts() == ["0", "1", "2"]


def test_env_key_strict_form_reads_the_variable(monkeypatch) -> None:
    monkeypatch.setenv("LLM_KEY", "key-one-1111")
    recorder = RecordingTransport([200])
    connection = _connection({"base_url": _URL, "model": "m", "auth": {"api_key_env": "LLM_KEY"}})
    bearer = bearer_for_connection(connection)
    assert isinstance(bearer, EnvironmentKeyBearer) and bearer.required
    llm = build_chat_model(connection, bearer, transport=recorder.transport)
    assert _ask(llm, bearer) == "OK"
    assert recorder.authorizations() == ["Bearer key-one-1111"]


def test_token_service_renews_on_401_and_retries_once() -> None:
    """AC-6: 401 then 200 ⇒ the SAME request once more with the renewed bearer; the SDK saw
    one attempt (retry count 0 on both), the token service saw initial + renew."""
    service, connection, bearer = _token_service_setup(["tok-one-abcd", "tok-two-efgh"])
    recorder = RecordingTransport([401, 200])
    llm = build_chat_model(connection, bearer, transport=recorder.transport)
    assert _ask(llm, bearer) == "OK"
    assert recorder.authorizations() == ["Bearer tok-one-abcd", "Bearer tok-two-efgh"]
    assert recorder.retry_counts() == ["0", "0"]
    assert service.calls == 2


def test_second_401_surfaces_bearer_rejected_without_the_body() -> None:
    """AC-7 + AC-35: 401, 401 ⇒ exactly two requests and a BearerRejectedError that carries the
    last four characters and the host, never the token nor the echoing body."""
    service, connection, bearer = _token_service_setup(["tok-one-abcd", "tok-two-efgh"])
    recorder = RecordingTransport([401, 401], echo_bearer_in_401=True)
    llm = build_chat_model(connection, bearer, transport=recorder.transport)
    with pytest.raises(BearerRejectedError) as excinfo:
        _ask(llm, bearer)
    message = str(excinfo.value)
    assert len(recorder.requests) == 2
    assert "…efgh" in message and "llm.test" in message and "(status 401)" in message
    assert "tok-two-efgh" not in message and "tok-one-abcd" not in message
    assert "rejected Bearer" not in message and excinfo.value.__cause__ is None


def test_sdk_retries_reuse_the_renewed_token() -> None:
    """AC-8: after a renewal, a 429-then-200 pair sends the renewed bearer twice with no
    further token-service call."""
    service, connection, bearer = _token_service_setup(["tok-one-abcd", "tok-two-efgh"])
    first = RecordingTransport([401, 200])
    assert _ask(build_chat_model(connection, bearer, transport=first.transport), bearer) == "OK"
    second = RecordingTransport([429, 200])
    llm = build_chat_model(connection, bearer, max_retries=1, transport=second.transport)
    assert _ask(llm, bearer) == "OK"
    assert second.authorizations() == ["Bearer tok-two-efgh", "Bearer tok-two-efgh"]
    assert service.calls == 2


def test_persistent_401s_across_two_invokes_fetch_one_renewal() -> None:
    """AC-33 (transport half): 401 ×4 over two invokes ⇒ initial fetch + ONE renewal; the
    second renewal is inside _MIN_RENEW_INTERVAL_SECONDS and returns the cache."""
    service, connection, bearer = _token_service_setup(["t1", "t2", "t3"])
    for _ in range(2):
        recorder = RecordingTransport([401, 401])
        with pytest.raises(BearerRejectedError):
            _ask(build_chat_model(connection, bearer, transport=recorder.transport), bearer)
        assert len(recorder.requests) == 2
    assert service.calls == 2


def test_origin_change_still_sends_the_bearer() -> None:
    """AC-31 (wire half, D3/H1): an override on another origin receives the bearer."""
    service = FakeTokenService(["tok-one-abcd"])
    connection = _connection(
        {"base_url": "https://llm.internal/v1", "model": "m", "auth": {"token_url": _TOKEN_URL}},
        {"OPENAI_BASE_URL": _URL},
    )
    assert connection.origin_changed is True
    bearer = TokenServiceBearer(_TOKEN_URL, transport=service.transport, sleep=lambda _s: None)
    recorder = RecordingTransport([200])
    assert _ask(build_chat_model(connection, bearer, transport=recorder.transport), bearer) == "OK"
    assert recorder.authorizations() == ["Bearer tok-one-abcd"]
    assert str(recorder.requests[0].url).startswith(_URL)


def test_translate_auth_errors_covers_every_mode(monkeypatch) -> None:
    """AC-35: env-key and no-auth connections have no renewing flow, so a 401 is a raw SDK
    error — the boundary still yields a redacted BearerRejectedError."""
    monkeypatch.setenv("LLM_KEY", "key-one-1111")
    cases = [
        ({"base_url": _URL, "model": "m", "auth": {"api_key_env": "LLM_KEY"}}, "…1111"),
        ({"base_url": _URL, "model": "m"}, "…"),
    ]
    for block, mask in cases:
        recorder = RecordingTransport([401], echo_bearer_in_401=True)
        connection = _connection(block)
        bearer = bearer_for_connection(connection)
        with pytest.raises(BearerRejectedError) as excinfo:
            _ask(build_chat_model(connection, bearer, transport=recorder.transport), bearer)
        assert mask in str(excinfo.value) and "key-one-1111" not in str(excinfo.value)
        assert len(recorder.requests) == 1


def test_no_token_in_sdk_or_httpx_logs(caplog) -> None:
    """AC-9 (SDK half): the openai / httpx loggers at DEBUG never carry the token."""
    caplog.set_level(logging.DEBUG, logger="openai")
    caplog.set_level(logging.DEBUG, logger="httpx")
    caplog.set_level(logging.DEBUG, logger="pydocs-mcp.harness.ask-your-docs")
    service, connection, bearer = _token_service_setup(["tok-one-abcd", "tok-two-efgh"])
    recorder = RecordingTransport([401, 200])
    _ask(build_chat_model(connection, bearer, transport=recorder.transport), bearer)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "tok-one-abcd" not in text and "tok-two-efgh" not in text


def test_registry_shares_one_bearer_across_builds() -> None:
    """AC-40 (registry half): two builds for one identity share one bearer and one fetch."""
    service = FakeTokenService(["tok-one-abcd"])
    connection = _connection({"base_url": _URL, "model": "m", "auth": {"token_url": _TOKEN_URL}})
    shared = bearer_for_connection(connection)
    assert isinstance(shared, TokenServiceBearer)
    shared._transport = service.transport  # the registry built it; point it at the fake service
    shared._sleep = lambda _s: None
    for _ in range(2):
        recorder = RecordingTransport([200])
        llm = build_chat_model(
            connection, bearer_for_connection(connection), transport=recorder.transport
        )
        assert _ask(llm, shared) == "OK"
        assert recorder.authorizations() == ["Bearer tok-one-abcd"]
    assert service.calls == 1


def test_run_connection_test_passes_and_fails_redacted() -> None:
    """AC-43 (helper half, E11): a caption string on success and on failure; never a raise."""
    service, connection, bearer = _token_service_setup(["tok-one-abcd", "tok-two-efgh"])
    good = RecordingTransport([200], reply="OK")
    assert (
        asyncio.run(run_connection_test(connection, bearer, transport=good.transport))
        == "test passed: OK"
    )
    bad = RecordingTransport([401, 401], echo_bearer_in_401=True)
    result = asyncio.run(run_connection_test(connection, bearer, transport=bad.transport))
    assert result.startswith("test failed: BearerRejectedError:")
    assert "tok-" not in result and "rejected Bearer" not in result
    down = RecordingTransport([httpx.ConnectError("refused")])
    result = asyncio.run(run_connection_test(connection, bearer, transport=down.transport))
    assert result.startswith("test failed: APIConnectionError:")
