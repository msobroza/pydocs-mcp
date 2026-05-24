"""EmbeddingConfig + AppConfig.embedding (spec §5.10)."""
import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig, EmbeddingConfig


def test_embedding_config_defaults() -> None:
    cfg = EmbeddingConfig()
    assert cfg.provider == "fastembed"
    assert cfg.model_name == "BAAI/bge-small-en-v1.5"
    assert cfg.dim == 384
    assert cfg.batch_size == 32
    assert cfg.bit_width == 4


def test_embedding_config_provider_literal_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        EmbeddingConfig(provider="cohere")  # type: ignore[arg-type]


def test_embedding_config_batch_size_min_1() -> None:
    with pytest.raises(ValidationError):
        EmbeddingConfig(batch_size=0)


def test_embedding_config_bit_width_range_1_to_8() -> None:
    EmbeddingConfig(bit_width=1)
    EmbeddingConfig(bit_width=8)
    with pytest.raises(ValidationError):
        EmbeddingConfig(bit_width=0)
    with pytest.raises(ValidationError):
        EmbeddingConfig(bit_width=9)


def test_appconfig_load_exposes_embedding_block() -> None:
    cfg = AppConfig.load()
    assert isinstance(cfg.embedding, EmbeddingConfig)
    assert cfg.embedding.provider == "fastembed"
    assert cfg.embedding.model_name == "BAAI/bge-small-en-v1.5"
