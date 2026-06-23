"""AC-1: OpenAiLlmClient implements LlmClient with both async + sync."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import RateLimitError

from pydocs_mcp.retrieval.llm_clients.openai import OpenAiLlmClient
from pydocs_mcp.storage.protocols import LlmClient


def test_openai_client_satisfies_protocol() -> None:
    # NB: with lazy SDK construction (post final-review fix), no API key
    # is needed at OpenAiLlmClient() construction — the SDK clients are
    # built on first chat()/chat_sync() call. So the Protocol-conformance
    # check can run without any env-var setup.
    client = OpenAiLlmClient(model_name="gpt-4o-mini")
    assert isinstance(client, LlmClient)
    assert client.model_name == "gpt-4o-mini"


def test_construction_without_api_key_does_not_raise() -> None:
    """Lazy SDK construction means missing OPENAI_API_KEY at startup is
    fine — error only surfaces on first chat() call. This is what lets
    a hybrid-only deployment (no LLM step in YAML) start clean."""
    # No env-var setup, no api_key kwarg — must not raise.
    client = OpenAiLlmClient(model_name="gpt-4o-mini")
    assert client.api_key is None
    # Lazy fields are None until the first chat() / chat_sync() call.
    assert client._async is None
    assert client._sync is None


@pytest.mark.asyncio
async def test_chat_async_calls_openai_with_expected_args() -> None:
    """chat() passes model + messages + response_format to AsyncOpenAI."""
    client = OpenAiLlmClient(model_name="gpt-4o-mini", api_key="test-key")
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="hi"))]
    # Trigger lazy construction so we can patch the cached SDK client.
    sdk = client._async_client()
    with patch.object(
        sdk.chat.completions,
        "create",
        new=AsyncMock(return_value=fake_completion),
    ) as mock_create:
        result = await client.chat(
            [{"role": "user", "content": "hello"}],
            response_format="json_object",
            temperature=0.5,
        )
    assert result == "hi"
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert call_kwargs["response_format"] == {"type": "json_object"}
    assert call_kwargs["temperature"] == 0.5


def test_chat_sync_calls_openai_with_expected_args() -> None:
    client = OpenAiLlmClient(model_name="gpt-4o-mini", api_key="test-key")
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="sync-hi"))]
    sdk = client._sync_client()
    with patch.object(
        sdk.chat.completions,
        "create",
        return_value=fake_completion,
    ) as mock_create:
        result = client.chat_sync(
            [{"role": "user", "content": "ping"}],
        )
    assert result == "sync-hi"
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    # Default response_format is "text" -> no json_object wrapper
    assert call_kwargs.get("response_format") is None


@pytest.mark.asyncio
async def test_chat_async_omits_temperature_for_reasoning_model() -> None:
    """Reasoning models (gpt-5+, o-series) only accept the default temperature;
    sending an explicit value 400s. chat() must OMIT the temperature kwarg for
    them, letting the model default stand, while still passing model + messages
    + response_format."""
    client = OpenAiLlmClient(model_name="gpt-5.5", api_key="test-key")
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="hi"))]
    sdk = client._async_client()
    with patch.object(
        sdk.chat.completions,
        "create",
        new=AsyncMock(return_value=fake_completion),
    ) as mock_create:
        result = await client.chat(
            [{"role": "user", "content": "hello"}],
            response_format="json_object",
            temperature=0.0,
        )
    assert result == "hi"
    call_kwargs = mock_create.call_args.kwargs
    assert "temperature" not in call_kwargs
    # None cap must be omitted, not sent as max_tokens: null (reasoning 400s).
    assert "max_tokens" not in call_kwargs
    assert "max_completion_tokens" not in call_kwargs
    assert call_kwargs["model"] == "gpt-5.5"
    assert call_kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_chat_async_maps_max_tokens_to_completion_for_reasoning_model() -> None:
    """A set token cap must go to max_completion_tokens (not the legacy
    max_tokens, which reasoning models reject) for reasoning models."""
    client = OpenAiLlmClient(model_name="gpt-5.5", api_key="test-key")
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="x"))]
    sdk = client._async_client()
    with patch.object(
        sdk.chat.completions,
        "create",
        new=AsyncMock(return_value=fake_completion),
    ) as mock_create:
        await client.chat([{"role": "user", "content": "hi"}], max_tokens=256)
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["max_completion_tokens"] == 256
    assert "max_tokens" not in call_kwargs


@pytest.mark.asyncio
async def test_chat_async_standard_model_uses_legacy_max_tokens() -> None:
    """Standard models keep the legacy max_tokens param when a cap is set."""
    client = OpenAiLlmClient(model_name="gpt-4o-mini", api_key="test-key")
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="x"))]
    sdk = client._async_client()
    with patch.object(
        sdk.chat.completions,
        "create",
        new=AsyncMock(return_value=fake_completion),
    ) as mock_create:
        await client.chat([{"role": "user", "content": "hi"}], max_tokens=256)
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["max_tokens"] == 256
    assert "max_completion_tokens" not in call_kwargs


def test_chat_sync_omits_temperature_for_reasoning_model() -> None:
    """Sync path mirrors the async reasoning-model temperature omission."""
    client = OpenAiLlmClient(model_name="o3-mini", api_key="test-key")
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="x"))]
    sdk = client._sync_client()
    with patch.object(
        sdk.chat.completions,
        "create",
        return_value=fake_completion,
    ) as mock_create:
        client.chat_sync(
            [{"role": "user", "content": "ping"}],
            temperature=0.0,
        )
    assert "temperature" not in mock_create.call_args.kwargs


@pytest.mark.asyncio
async def test_chat_async_keeps_temperature_for_standard_model() -> None:
    """Non-reasoning models (gpt-4o-mini) must STILL receive the caller's
    temperature — the omission applies only to reasoning models."""
    client = OpenAiLlmClient(model_name="gpt-4o-mini", api_key="test-key")
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="hi"))]
    sdk = client._async_client()
    with patch.object(
        sdk.chat.completions,
        "create",
        new=AsyncMock(return_value=fake_completion),
    ) as mock_create:
        await client.chat(
            [{"role": "user", "content": "hello"}],
            temperature=0.0,
        )
    assert mock_create.call_args.kwargs["temperature"] == 0.0


@pytest.mark.asyncio
async def test_openai_client_retries_on_rate_limit() -> None:
    """Transient RateLimitError must be retried with backoff; a 3rd success
    yields the response. Persistent failures would still surface on attempt 3."""
    client = OpenAiLlmClient(model_name="gpt-4o-mini", api_key="test-key")
    call_count = {"n": 0}

    async def flaky(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RateLimitError(
                message="rate limit",
                response=MagicMock(),
                body=None,
            )
        return MagicMock(
            choices=[MagicMock(message=MagicMock(content="ok"))],
        )

    sdk = client._async_client()
    # Patch asyncio.sleep so the backoff doesn't slow the test down.
    with (
        patch.object(sdk.chat.completions, "create", new=flaky),
        patch(
            "pydocs_mcp.retrieval.llm_clients.openai.asyncio.sleep",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await client.chat(
            [{"role": "user", "content": "x"}],
        )
    assert call_count["n"] == 3
    assert "ok" in result
