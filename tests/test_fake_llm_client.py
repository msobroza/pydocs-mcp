"""FakeLlmClient delivers canned responses without network calls."""

from __future__ import annotations

import pytest

from pydocs_mcp.retrieval.protocols import LlmClient
from tests._fakes import FakeLlmClient


def test_fake_satisfies_protocol() -> None:
    client = FakeLlmClient(responses={})
    assert isinstance(client, LlmClient)


@pytest.mark.asyncio
async def test_fake_chat_returns_canned() -> None:
    client = FakeLlmClient(
        model_name="fake-model",
        responses={"hello": "world"},
    )
    result = await client.chat(
        [{"role": "user", "content": "hello"}],
    )
    assert result == "world"


def test_fake_chat_sync_returns_canned() -> None:
    client = FakeLlmClient(
        responses={"ping": "pong"},
    )
    result = client.chat_sync(
        [{"role": "user", "content": "ping"}],
    )
    assert result == "pong"


@pytest.mark.asyncio
async def test_fake_raises_on_unknown_key() -> None:
    """Unknown keys raise KeyError with diagnostic context."""
    client = FakeLlmClient(responses={"hi": "hello"})
    with pytest.raises(KeyError, match="not-in-responses"):
        await client.chat([{"role": "user", "content": "not-in-responses"}])
