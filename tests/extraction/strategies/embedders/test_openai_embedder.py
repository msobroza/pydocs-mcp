"""OpenAIEmbedder construction + API key check + optional-dep guard (AC-14)."""
import sys
from unittest.mock import MagicMock, patch

import pytest


def test_construction_without_api_key_raises_runtime_error(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    mock_openai = MagicMock()
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.openai", None,
    )
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            OpenAIEmbedder(model_name="text-embedding-3-small", dim=1536)
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.openai", None,
    )


def test_construction_with_api_key_succeeds(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_openai = MagicMock()
    mock_openai.AsyncOpenAI = MagicMock()
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.openai", None,
    )
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )
        emb = OpenAIEmbedder(model_name="text-embedding-3-small", dim=1536)
        assert emb.dim == 1536
        assert emb.model_name == "text-embedding-3-small"
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.openai", None,
    )
