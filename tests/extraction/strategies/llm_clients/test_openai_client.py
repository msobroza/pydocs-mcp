"""AC-1: OpenAiLlmClient implements LlmClient with both async + sync."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pydocs_mcp.extraction.strategies.llm_clients.openai import OpenAiLlmClient
from pydocs_mcp.storage.protocols import LlmClient


def test_openai_client_satisfies_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    # WHY: openai>=1.40 SDK raises if neither api_key nor OPENAI_API_KEY is
    # present at construction; set a dummy env var so the protocol check
    # doesn't depend on the developer's real credentials.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-protocol-check")
    client = OpenAiLlmClient(model_name="gpt-4o-mini")
    assert isinstance(client, LlmClient)
    assert client.model_name == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_chat_async_calls_openai_with_expected_args() -> None:
    """chat() passes model + messages + response_format to AsyncOpenAI."""
    client = OpenAiLlmClient(model_name="gpt-4o-mini", api_key="test-key")
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="hi"))]
    with patch.object(
        client._async_client.chat.completions,
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
    with patch.object(
        client._sync_client.chat.completions,
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
