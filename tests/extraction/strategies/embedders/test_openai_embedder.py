"""OpenAIEmbedder construction + API key check + optional-dep guard (AC-14).

Also covers the OpenAI-compatible-endpoint knobs (``base_url`` /
``api_key_env`` / ``send_dimensions``) used to reach non-OpenAI services
such as OpenRouter for Mistral codestral-embed.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_construction_without_api_key_raises_runtime_error(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    mock_openai = MagicMock()
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.openai",
        None,
    )
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )

        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            OpenAIEmbedder(model_name="text-embedding-3-small", dim=1536)
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.openai",
        None,
    )


def test_construction_with_api_key_succeeds(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_openai = MagicMock()
    mock_openai.AsyncOpenAI = MagicMock()
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.openai",
        None,
    )
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )

        emb = OpenAIEmbedder(model_name="text-embedding-3-small", dim=1536)
        assert emb.dim == 1536
        assert emb.model_name == "text-embedding-3-small"
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.openai",
        None,
    )


def test_base_url_passed_to_client_when_set(monkeypatch) -> None:
    # OpenRouter/codestral path: base_url must reach the AsyncOpenAI client so
    # the request hits openrouter.ai instead of api.openai.com.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    mock_openai = MagicMock()
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.openai", None)
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )

        OpenAIEmbedder(
            model_name="mistralai/codestral-embed-2505",
            dim=1536,
            base_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
        )
    mock_openai.AsyncOpenAI.assert_called_once_with(
        api_key="sk-or-test", base_url="https://openrouter.ai/api/v1"
    )
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.openai", None)


def test_base_url_omitted_when_none(monkeypatch) -> None:
    # Default path: no base_url kwarg -> the SDK falls back to its own default
    # endpoint. Passing base_url=None explicitly would override any user-set
    # OPENAI_BASE_URL, so it must be omitted, not passed as None.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_openai = MagicMock()
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.openai", None)
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )

        OpenAIEmbedder(model_name="text-embedding-3-small", dim=1536)
    mock_openai.AsyncOpenAI.assert_called_once_with(api_key="sk-test")
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.openai", None)


def test_custom_api_key_env_is_read(monkeypatch) -> None:
    # api_key_env names the var the key is read from (so OPENROUTER_API_KEY
    # never has to masquerade as OPENAI_API_KEY).
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xyz")
    mock_openai = MagicMock()
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.openai", None)
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )

        OpenAIEmbedder(model_name="m", dim=1536, api_key_env="OPENROUTER_API_KEY")
    mock_openai.AsyncOpenAI.assert_called_once_with(api_key="sk-or-xyz")
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.openai", None)


def test_missing_custom_api_key_env_raises_naming_that_var(monkeypatch) -> None:
    # The error must name the configured env var, not the OPENAI default —
    # a user who set api_key_env: OPENROUTER_API_KEY needs to be told THAT var
    # is missing, even if OPENAI_API_KEY happens to be present.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-present-but-irrelevant")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    mock_openai = MagicMock()
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.openai", None)
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )

        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
            OpenAIEmbedder(model_name="m", dim=1536, api_key_env="OPENROUTER_API_KEY")
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.openai", None)


async def test_send_dimensions_true_passes_dimensions(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_openai = MagicMock()
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.openai", None)
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )

        emb = OpenAIEmbedder(model_name="text-embedding-3-small", dim=1536, send_dimensions=True)
        fake_resp = MagicMock()
        fake_resp.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]
        emb._client.embeddings.create = AsyncMock(return_value=fake_resp)
        await emb.embed_query("hello")
    _, kwargs = emb._client.embeddings.create.call_args
    assert kwargs["dimensions"] == 1536
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.openai", None)


async def test_send_dimensions_false_omits_dimensions(monkeypatch) -> None:
    # codestral via OpenRouter rejects OpenAI's Matryoshka `dimensions` param;
    # with send_dimensions=False it must not appear in the request at all.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    mock_openai = MagicMock()
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.openai", None)
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )

        emb = OpenAIEmbedder(
            model_name="mistralai/codestral-embed-2505",
            dim=1536,
            api_key_env="OPENROUTER_API_KEY",
            send_dimensions=False,
        )
        fake_resp = MagicMock()
        fake_resp.data = [MagicMock(embedding=[0.1, 0.2])]
        emb._client.embeddings.create = AsyncMock(return_value=fake_resp)
        await emb.embed_query("hello")
    _, kwargs = emb._client.embeddings.create.call_args
    assert "dimensions" not in kwargs
    assert kwargs["model"] == "mistralai/codestral-embed-2505"
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.openai", None)
