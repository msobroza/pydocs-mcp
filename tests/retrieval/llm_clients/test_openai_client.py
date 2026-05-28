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
