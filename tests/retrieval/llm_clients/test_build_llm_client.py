"""REGRESSION: LlmConfig.temperature / max_tokens must reach the request.

build_llm_client(cfg) previously dropped cfg.temperature and cfg.max_tokens
when constructing OpenAiLlmClient — a YAML overlay setting
``llm: {temperature: 0.2, max_tokens: 1024}`` (the documented A/B-sweep
surface, CLAUDE.md "MCP API surface vs YAML configuration") silently did
nothing: every request still hit the API with temperature=0.0 and no cap.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pydocs_mcp.retrieval.config import LlmConfig
from pydocs_mcp.retrieval.llm_clients import build_llm_client


@pytest.mark.asyncio
async def test_build_llm_client_threads_configured_temperature_and_max_tokens() -> None:
    """A configured temperature/max_tokens must reach chat.completions.create
    when the step's chat() call doesn't override them."""
    cfg = LlmConfig(temperature=0.2, max_tokens=512, api_key="test-key")
    client = build_llm_client(cfg)

    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="hi"))]
    sdk = client._async_client()  # type: ignore[attr-defined]
    with patch.object(
        sdk.chat.completions,
        "create",
        new=AsyncMock(return_value=fake_completion),
    ) as mock_create:
        # Mirrors LlmTreeReasoningStep.run(): calls chat() without an
        # explicit temperature/max_tokens override, relying on the
        # client's configured values.
        await client.chat(
            [{"role": "user", "content": "hello"}],
            response_format="json_object",
        )
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["temperature"] == 0.2
    assert call_kwargs["max_tokens"] == 512


def test_build_llm_client_defaults_match_llm_config_defaults() -> None:
    """No YAML overlay -> LlmConfig defaults (temperature=0.0, max_tokens=None)
    must still be the client's effective defaults, not silently dropped."""
    cfg = LlmConfig()
    client = build_llm_client(cfg)
    assert client.temperature == cfg.temperature  # type: ignore[attr-defined]
    assert client.max_tokens == cfg.max_tokens  # type: ignore[attr-defined]
