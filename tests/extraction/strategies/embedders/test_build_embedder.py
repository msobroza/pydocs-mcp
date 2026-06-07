"""build_embedder factory (AC-15, AC-16)."""

import pytest

from pydocs_mcp.extraction.strategies.embedders import build_embedder
from pydocs_mcp.retrieval.config import EmbeddingConfig


def test_unknown_provider_raises_valueerror() -> None:
    cfg = EmbeddingConfig.model_construct(provider="cohere")  # bypass Literal at runtime
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        build_embedder(cfg)


def test_build_embedder_onnx_returns_onnx_embedder() -> None:
    from pydocs_mcp.retrieval.config import EmbeddingConfig
    from pydocs_mcp.extraction.strategies.embedders import build_embedder
    from pydocs_mcp.extraction.strategies.embedders.onnx import OnnxEmbedder

    # Patch __post_init__ to a no-op so build_embedder does NOT download a model.
    import pydocs_mcp.extraction.strategies.embedders.onnx as onnx_mod

    orig = onnx_mod.OnnxEmbedder.__post_init__
    onnx_mod.OnnxEmbedder.__post_init__ = lambda self: None  # type: ignore[assignment]
    try:
        e = build_embedder(
            EmbeddingConfig(
                provider="onnx",
                model_name="onnx-community/Qwen3-Embedding-0.6B-ONNX",
                dim=1024,
            )
        )
        assert isinstance(e, OnnxEmbedder)
        assert e.onnx_file == "onnx/model_fp16.onnx"
        assert e.dim == 1024 and e.model_name == "onnx-community/Qwen3-Embedding-0.6B-ONNX"
    finally:
        onnx_mod.OnnxEmbedder.__post_init__ = orig  # type: ignore[assignment]


def test_build_embedder_passes_device_to_fastembed() -> None:
    import sys
    from unittest.mock import MagicMock, patch

    captured = {}

    def _fake_text_embedding(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = _fake_text_embedding

    from pydocs_mcp.retrieval.config import EmbeddingConfig

    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)
    with patch.dict(sys.modules, {"fastembed": mock_fastembed}):
        from pydocs_mcp.extraction.strategies.embedders import build_embedder

        build_embedder(EmbeddingConfig(provider="fastembed", device="cuda"))

    assert captured["providers"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)
