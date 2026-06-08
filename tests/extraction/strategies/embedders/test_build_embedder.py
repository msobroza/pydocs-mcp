"""build_embedder factory (AC-15, AC-16)."""

import pytest

from pydocs_mcp.extraction.strategies.embedders import build_embedder
from pydocs_mcp.retrieval.config import EmbeddingConfig


def test_unknown_provider_raises_valueerror() -> None:
    cfg = EmbeddingConfig.model_construct(provider="cohere")  # bypass Literal at runtime
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        build_embedder(cfg)


def _build_st(cfg: EmbeddingConfig):
    """build_embedder(cfg) with the real model load patched out.

    Patching ``__post_init__`` to a no-op keeps the test hermetic — no torch
    import, no model download — while still exercising build_embedder's field
    threading (the dataclass fields are set from the kwargs it passes).
    """
    import pydocs_mcp.extraction.strategies.embedders.sentence_transformers as st_mod

    orig = st_mod.SentenceTransformersEmbedder.__post_init__
    st_mod.SentenceTransformersEmbedder.__post_init__ = lambda self: None  # type: ignore[assignment]
    try:
        return build_embedder(cfg)
    finally:
        st_mod.SentenceTransformersEmbedder.__post_init__ = orig  # type: ignore[assignment]


def test_build_embedder_sentence_transformers_returns_st_embedder() -> None:
    from pydocs_mcp.extraction.strategies.embedders.sentence_transformers import (
        SentenceTransformersEmbedder,
    )

    e = _build_st(
        EmbeddingConfig(
            provider="sentence_transformers",
            model_name="Qwen/Qwen3-Embedding-0.6B",
            dim=1024,
            device="cuda",
            batch_size=8,
        )
    )
    assert isinstance(e, SentenceTransformersEmbedder)
    assert e.dim == 1024
    assert e.model_name == "Qwen/Qwen3-Embedding-0.6B"
    # Device is threaded through from config.
    assert e.device == "cuda"
    assert e.batch_size == 8
    # New knobs unset in config -> embedder keeps its own defaults.
    assert e.normalize is True
    assert e.query_prompt_name is None
    # max_seq_length: config default None means "inherit", so build_embedder
    # does NOT pass it and the embedder keeps its own 2048 cap.
    assert e.max_seq_length == 2048


def test_build_embedder_st_threads_seq_normalize_prompt() -> None:
    e = _build_st(
        EmbeddingConfig(
            provider="sentence_transformers",
            model_name="Qwen/Qwen3-Embedding-0.6B",
            dim=1024,
            max_seq_length=512,
            normalize=False,
            query_prompt_name="query",
        )
    )
    assert e.max_seq_length == 512
    assert e.normalize is False
    assert e.query_prompt_name == "query"


def test_build_embedder_passes_device_to_fastembed() -> None:
    import sys
    from unittest.mock import MagicMock, patch

    captured = {}

    def _fake_text_embedding(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = _fake_text_embedding

    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)
    with patch.dict(sys.modules, {"fastembed": mock_fastembed}):
        build_embedder(EmbeddingConfig(provider="fastembed", device="cuda"))

    assert captured["providers"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)
