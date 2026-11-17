"""OpenAiLlmClient on the HTTP wire, through the real openai SDK.

The sibling ``test_openai_client.py`` patches SDK objects, so it stays green
whatever the SDK puts on the wire. These tests put an SDK client built on
:class:`~tests._fakes.FakeOpenAIWire` (``httpx.MockTransport``) into the
client's lazily cached ``_async`` / ``_sync`` fields and assert the request
bodies themselves — the shape the openai 1.x → 2.x bump must not move.

Kinds: every test is a characterization (green on openai 1.109.1 and 2.x)
except ``test_empty_api_key_fails_at_first_chat_naming_missing_credentials``,
a lock pin of the empty-key rejection openai 2.34 introduced: red on 1.x and
on openai 2.26–2.33, which send the request with no ``Authorization`` header.
"""

from __future__ import annotations

from typing import Any

import openai
import pytest

from pydocs_mcp.retrieval.llm_clients import openai as llm_client_module
from pydocs_mcp.retrieval.llm_clients.openai import _RETRY_BACKOFF, OpenAiLlmClient
from tests._fakes import FakeOpenAIWire, SleepRecorder

_MESSAGES = [{"role": "user", "content": "héllo"}]
_STANDARD_MODEL = "gpt-4o-mini"
_REASONING_MODEL = "gpt-5.5"
_PATHS = ("async", "sync")


def _wired_client(wire: FakeOpenAIWire, model_name: str = _STANDARD_MODEL) -> OpenAiLlmClient:
    client = OpenAiLlmClient(model_name=model_name, api_key="sk-test")
    client._async = wire.async_openai()
    client._sync = wire.sync_openai()
    return client


async def _send(client: OpenAiLlmClient, path: str, **kwargs: Any) -> str:
    """``chat()`` or ``chat_sync()`` — the two surfaces must put the same bytes on the wire."""
    if path == "async":
        return await client.chat(_MESSAGES, **kwargs)
    return client.chat_sync(_MESSAGES, **kwargs)


@pytest.mark.parametrize("path", _PATHS)
async def test_json_mode_sends_json_object_response_format(path: str) -> None:
    wire = FakeOpenAIWire()
    reply = await _send(_wired_client(wire), path, response_format="json_object")
    (request,) = wire.requests
    assert request.path == "/v1/chat/completions"
    assert request.body["response_format"] == {"type": "json_object"}
    assert request.body["model"] == _STANDARD_MODEL
    assert request.body["messages"] == _MESSAGES
    assert reply == "wire-reply"


@pytest.mark.parametrize("path", _PATHS)
async def test_text_mode_sends_response_format_as_null(path: str) -> None:
    # The SDK forwards an explicit None as JSON null rather than dropping the
    # key; servers that reject null would 400 here, so the bytes are pinned.
    wire = FakeOpenAIWire()
    await _send(_wired_client(wire), path)
    (request,) = wire.requests
    assert "response_format" in request.body
    assert request.body["response_format"] is None


@pytest.mark.parametrize("path", _PATHS)
async def test_standard_model_sends_temperature_and_max_tokens(path: str) -> None:
    wire = FakeOpenAIWire()
    await _send(_wired_client(wire), path, temperature=0.5, max_tokens=256)
    (request,) = wire.requests
    assert request.body["temperature"] == 0.5
    assert request.body["max_tokens"] == 256
    assert "max_completion_tokens" not in request.body


@pytest.mark.parametrize("path", _PATHS)
async def test_reasoning_model_sends_max_completion_tokens_and_no_temperature(path: str) -> None:
    wire = FakeOpenAIWire()
    await _send(_wired_client(wire, _REASONING_MODEL), path, temperature=0.0, max_tokens=256)
    (request,) = wire.requests
    assert request.body["model"] == _REASONING_MODEL
    assert "temperature" not in request.body
    assert "max_tokens" not in request.body
    assert request.body["max_completion_tokens"] == 256


@pytest.mark.parametrize("path", _PATHS)
async def test_null_content_reply_yields_empty_string(path: str) -> None:
    wire = FakeOpenAIWire(chat_content=None)
    reply = await _send(_wired_client(wire), path)
    assert reply == ""


async def test_async_429_then_200_is_retried_by_the_client_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The injected SDK client has max_retries=0, so the second request on the
    # wire can only come from OpenAiLlmClient's own RateLimitError loop.
    wire = FakeOpenAIWire(chat_statuses=[429, 200])
    client = _wired_client(wire)
    sleeps = SleepRecorder()
    monkeypatch.setattr(llm_client_module.asyncio, "sleep", sleeps.async_sleep)
    reply = await client.chat(_MESSAGES)
    assert reply == "wire-reply"
    assert len(wire.requests) == 2
    assert wire.requests[0].body == wire.requests[1].body
    assert sleeps.delays == [_RETRY_BACKOFF]


def test_sync_429_then_200_is_retried_by_the_client_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = FakeOpenAIWire(chat_statuses=[429, 200])
    client = _wired_client(wire)
    sleeps = SleepRecorder()
    monkeypatch.setattr(llm_client_module.time, "sleep", sleeps)
    reply = client.chat_sync(_MESSAGES)
    assert reply == "wire-reply"
    assert len(wire.requests) == 2
    assert sleeps.delays == [_RETRY_BACKOFF]


def test_real_sdk_clients_take_the_base_url_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The unpatched construction path: OpenAiLlmClient passes only api_key, so
    # an OpenAI-compatible endpoint is reached through OPENAI_BASE_URL.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://llm-endpoint.test/v1")
    client = OpenAiLlmClient(model_name=_STANDARD_MODEL)
    assert str(client._async_client().base_url) == "http://llm-endpoint.test/v1/"
    assert str(client._sync_client().base_url) == "http://llm-endpoint.test/v1/"


# A loopback port nothing listens on (discard): before openai 2.34 the client
# is built with the empty key and sends a credential-less request, which this
# address refuses — so even a red run never leaves the machine. From 2.34 the
# SDK raises before any request, so no socket is opened at all.
_REFUSING_LOOPBACK_BASE_URL = "http://127.0.0.1:9/v1"


async def test_empty_api_key_fails_at_first_chat_naming_missing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", _REFUSING_LOOPBACK_BASE_URL)
    client = OpenAiLlmClient(model_name=_STANDARD_MODEL, api_key="")
    with pytest.raises(openai.OpenAIError, match="Missing credentials"):
        await client.chat(_MESSAGES)
