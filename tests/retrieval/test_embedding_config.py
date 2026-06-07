"""EmbeddingConfig + AppConfig.embedding (spec §5.10)."""

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig, EmbeddingConfig


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Isolate each test from ambient ``PYDOCS_*`` env vars and a user file.

    Mirrors the fixture in ``test_config.py`` / ``test_reference_graph_config.py``
    so ``AppConfig.load()`` resolves only the shipped baseline unless a test
    explicitly sets env or supplies an explicit_path.
    """
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("PYDOCS_LOG_LEVEL", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./pydocs-mcp.yaml
    yield


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


def test_dim_mismatch_with_known_model_raises() -> None:
    with pytest.raises(ValidationError, match="does not match the known dimension"):
        EmbeddingConfig(model_name="BAAI/bge-small-en-v1.5", dim=1024)


def test_unknown_model_name_skips_dim_check() -> None:
    # Custom model not in _KNOWN_MODEL_DIMS — caller is on the hook.
    cfg = EmbeddingConfig(model_name="my-custom-model", dim=512)
    assert cfg.dim == 512


def test_env_nested_delimiter_overrides_embedding_field(monkeypatch) -> None:
    monkeypatch.setenv("PYDOCS_EMBEDDING__BATCH_SIZE", "64")
    cfg = AppConfig.load()
    assert cfg.embedding.batch_size == 64


def test_embedding_config_dim_must_be_multiple_of_8() -> None:
    # Valid multiples of 8 — covers the small end (8), shipped default (384),
    # and the largest OpenAI dim (3072 — passes via the field validator,
    # then would fail the known-model cross-check if model_name didn't
    # match, hence the dummy model_name here).
    EmbeddingConfig(model_name="my-custom-model", dim=8)
    EmbeddingConfig(dim=384)
    EmbeddingConfig(model_name="text-embedding-3-small", dim=1536)
    # Non-multiples raise at load time — not at first write.
    with pytest.raises(ValidationError, match="multiple of 8"):
        EmbeddingConfig(dim=100)
    with pytest.raises(ValidationError, match="multiple of 8"):
        EmbeddingConfig(dim=7)


def test_embedding_config_rejects_removed_onnx_provider() -> None:
    # The onnx provider was removed; the Literal must reject it.
    with pytest.raises(ValidationError):
        EmbeddingConfig(provider="onnx")  # type: ignore[arg-type]


def test_embedding_config_st_knob_defaults() -> None:
    cfg = EmbeddingConfig()
    assert cfg.max_seq_length is None  # None = inherit the embedder's own cap
    assert cfg.normalize is True
    assert cfg.query_prompt_name is None


def test_embedding_config_accepts_sentence_transformers_provider() -> None:
    from pydocs_mcp.retrieval.config import EmbeddingConfig

    cfg = EmbeddingConfig(
        provider="sentence_transformers",
        model_name="Qwen/Qwen3-Embedding-0.6B",
        dim=1024,
    )
    assert cfg.provider == "sentence_transformers"
    assert cfg.model_name == "Qwen/Qwen3-Embedding-0.6B"
    assert cfg.dim == 1024


def test_embedding_config_sentence_transformers_known_dim_enforced() -> None:
    import pytest

    from pydocs_mcp.retrieval.config import EmbeddingConfig

    with pytest.raises(Exception):
        EmbeddingConfig(
            provider="sentence_transformers",
            model_name="Qwen/Qwen3-Embedding-0.6B",
            dim=768,
        )


def test_embedding_config_hash_folds_max_seq_length_and_normalize() -> None:
    # max_seq_length and normalize change the produced vectors, so editing
    # either must invalidate the chunk cache (change the hash).
    base = EmbeddingConfig(
        provider="sentence_transformers", model_name="Qwen/Qwen3-Embedding-0.6B", dim=1024
    )
    diff_seq = EmbeddingConfig(
        provider="sentence_transformers",
        model_name="Qwen/Qwen3-Embedding-0.6B",
        dim=1024,
        max_seq_length=512,
    )
    diff_norm = EmbeddingConfig(
        provider="sentence_transformers",
        model_name="Qwen/Qwen3-Embedding-0.6B",
        dim=1024,
        normalize=False,
    )
    assert base.compute_pipeline_hash() != diff_seq.compute_pipeline_hash()
    assert base.compute_pipeline_hash() != diff_norm.compute_pipeline_hash()


def test_query_prompt_name_excluded_from_pipeline_hash() -> None:
    # query_prompt_name only shapes the query embedding, never the stored
    # document vectors — so it must NOT invalidate the chunk cache.
    base = EmbeddingConfig(
        provider="sentence_transformers", model_name="Qwen/Qwen3-Embedding-0.6B", dim=1024
    )
    with_prompt = EmbeddingConfig(
        provider="sentence_transformers",
        model_name="Qwen/Qwen3-Embedding-0.6B",
        dim=1024,
        query_prompt_name="query",
    )
    assert base.compute_pipeline_hash() == with_prompt.compute_pipeline_hash()


def test_embedding_device_defaults_to_cpu_and_accepts_cuda() -> None:
    from pydocs_mcp.retrieval.config import EmbeddingConfig

    assert EmbeddingConfig().device == "cpu"
    assert EmbeddingConfig(device="cuda").device == "cuda"


def test_embedding_device_excluded_from_pipeline_hash() -> None:
    """Device is a runtime latency knob, not part of vector identity."""
    from pydocs_mcp.retrieval.config import EmbeddingConfig

    cpu = EmbeddingConfig(device="cpu")
    cuda = EmbeddingConfig(device="cuda")
    assert cpu.compute_pipeline_hash() == cuda.compute_pipeline_hash()


def test_embedding_device_rejects_unknown() -> None:
    import pytest
    from pydantic import ValidationError

    from pydocs_mcp.retrieval.config import EmbeddingConfig

    with pytest.raises(ValidationError):
        EmbeddingConfig(device="tpu")  # type: ignore[arg-type]
