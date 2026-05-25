"""build_embedder factory (AC-15, AC-16)."""
import pytest

from pydocs_mcp.extraction.strategies.embedders import build_embedder
from pydocs_mcp.retrieval.config import EmbeddingConfig


def test_unknown_provider_raises_valueerror() -> None:
    cfg = EmbeddingConfig.model_construct(provider="cohere")  # bypass Literal at runtime
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        build_embedder(cfg)
