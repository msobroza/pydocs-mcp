"""build_embedder factory + OptionalDepMissing (AC-15, AC-16)."""
import pytest

from pydocs_mcp.extraction.strategies.embedders import (
    OptionalDepMissing,
    build_embedder,
)
from pydocs_mcp.retrieval.config import EmbeddingConfig


def test_unknown_provider_raises_valueerror() -> None:
    cfg = EmbeddingConfig.model_construct(provider="cohere")  # bypass Literal at runtime
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        build_embedder(cfg)


def test_optional_dep_missing_is_distinct_exception_type() -> None:
    # Sanity: the OptionalDepMissing exception class is exported and is
    # distinct from RuntimeError/ImportError so callers can catch it.
    assert issubclass(OptionalDepMissing, Exception)
    assert OptionalDepMissing is not ImportError
