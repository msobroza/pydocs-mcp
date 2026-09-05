"""build_embedder factory (AC-15, AC-16)."""

import pytest

from pydocs_mcp.extraction.strategies.embedders import build_embedder
from pydocs_mcp.retrieval.config import EmbeddingConfig

# These tests assert the REAL factory's provider-selection behavior (they patch
# the heavy model load themselves), so opt out of the autouse
# build_embedder->MockEmbedder patch in tests/conftest.py.
pytestmark = pytest.mark.real_embedder


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


def test_st_backend_and_file_threaded() -> None:
    emb = _build_st(
        EmbeddingConfig(
            provider="sentence_transformers",
            model_name="m",
            dim=8,
            backend="openvino",
            model_file_name="openvino/openvino_model_qint8_quantized.xml",
        )
    )
    assert emb.backend == "openvino"
    assert emb.model_file_name == "openvino/openvino_model_qint8_quantized.xml"


def test_st_backend_defaults_thread_as_torch_none() -> None:
    emb = _build_st(EmbeddingConfig(provider="sentence_transformers", model_name="m", dim=8))
    assert emb.backend == "torch"
    assert emb.model_file_name is None


def test_build_fastembed_threads_local_recipe(tmp_path) -> None:
    import sys
    from unittest.mock import MagicMock, patch

    captured = {}

    class _Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_mod = MagicMock()
    fake_mod.FastEmbedEmbedder = _Fake
    with patch.dict(
        sys.modules,
        {"pydocs_mcp.extraction.strategies.embedders.fastembed": fake_mod},
    ):
        build_embedder(
            EmbeddingConfig(
                model_name=str(tmp_path),
                pooling="cls",
                normalize=False,
                model_file_name="onnx/model_q.onnx",
            )
        )

    assert captured["model_name"] == str(tmp_path)
    assert captured["pooling"] == "cls"
    assert captured["normalize"] is False
    assert captured["model_file_name"] == "onnx/model_q.onnx"


def test_build_openai_rejects_local_directory(tmp_path) -> None:
    import os
    import re

    env_before = (os.environ.get("HF_HUB_OFFLINE"), os.environ.get("TRANSFORMERS_OFFLINE"))
    cfg = EmbeddingConfig(provider="openai", model_name=str(tmp_path), dim=1536)
    with pytest.raises(ValueError, match=re.escape(str(tmp_path))):
        build_embedder(cfg)
    # The rejection must happen before any embedder work — no offline env
    # mutation (enable_hf_offline) may leak from this path.
    assert (os.environ.get("HF_HUB_OFFLINE"), os.environ.get("TRANSFORMERS_OFFLINE")) == env_before


def test_build_openai_threads_model_and_dim() -> None:
    # Kills the mutant class the registry review surfaced: a builder that
    # hardcodes the model id or sources dim from the wrong config field
    # survives every other test (the parity test compares provider NAMES
    # only, and the local-dir rejection test never reaches the return).
    import sys
    from unittest.mock import MagicMock, patch

    captured = {}

    class _Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_mod = MagicMock()
    fake_mod.OpenAIEmbedder = _Fake
    with patch.dict(
        sys.modules,
        {"pydocs_mcp.extraction.strategies.embedders.openai": fake_mod},
    ):
        build_embedder(
            EmbeddingConfig(provider="openai", model_name="text-embedding-3-small", dim=1536)
        )

    assert captured == {
        "model_name": "text-embedding-3-small",
        "dim": 1536,
        # Endpoint knobs thread through at their defaults (real OpenAI).
        "base_url": None,
        "api_key_env": None,
        "send_dimensions": True,
    }


def test_build_openai_threads_endpoint_knobs() -> None:
    # OpenAI-compatible endpoint (OpenRouter/codestral): base_url, api_key_env
    # and send_dimensions must reach OpenAIEmbedder verbatim from the config.
    import sys
    from unittest.mock import MagicMock, patch

    captured = {}

    class _Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_mod = MagicMock()
    fake_mod.OpenAIEmbedder = _Fake
    with patch.dict(
        sys.modules,
        {"pydocs_mcp.extraction.strategies.embedders.openai": fake_mod},
    ):
        build_embedder(
            EmbeddingConfig(
                provider="openai",
                model_name="mistralai/codestral-embed-2505",
                dim=1536,
                base_url="https://openrouter.ai/api/v1",
                api_key_env="OPENROUTER_API_KEY",
                send_dimensions=False,
            )
        )

    assert captured == {
        "model_name": "mistralai/codestral-embed-2505",
        "dim": 1536,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "send_dimensions": False,
    }
