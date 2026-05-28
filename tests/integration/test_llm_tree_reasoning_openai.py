"""Integration test against real OpenAI. Skipped without OPENAI_API_KEY."""

from __future__ import annotations

import json
import os

import pytest

from pydocs_mcp.retrieval.config import LlmConfig
from pydocs_mcp.retrieval.llm_clients.openai import OpenAiLlmClient

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="Requires OPENAI_API_KEY for integration test against real OpenAI.",
)


@pytest.mark.asyncio
async def test_openai_chat_returns_json_when_requested() -> None:
    """Smoke: real gpt-4o-mini call with json_object format returns JSON.

    Catches OpenAI SDK signature regressions that mocked unit tests miss.
    """
    cfg = LlmConfig(provider="openai", model_name="gpt-4o-mini", temperature=0.0)
    client = OpenAiLlmClient(model_name=cfg.model_name, api_key=cfg.api_key)
    response = await client.chat(
        [{"role": "user", "content": 'Return {"ok": true} as JSON.'}],
        response_format="json_object",
    )
    parsed = json.loads(response)
    assert parsed.get("ok") is True
