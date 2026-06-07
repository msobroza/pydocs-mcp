"""FastEmbedEmbedder construction (AC-13, AC-16)."""

import sys
from unittest.mock import MagicMock, patch


def test_fastembedembedder_construction_with_mocked_fastembed() -> None:
    """When fastembed is mocked, FastEmbedEmbedder constructs OK."""
    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = MagicMock()

    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.fastembed",
        None,
    )
    with patch.dict(sys.modules, {"fastembed": mock_fastembed}):
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )

        emb = FastEmbedEmbedder(model_name="BAAI/bge-small-en-v1.5", dim=384)
        assert emb.dim == 384
        assert emb.model_name == "BAAI/bge-small-en-v1.5"

    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.fastembed",
        None,
    )


def test_fastembed_cuda_passes_gpu_providers() -> None:
    """device='cuda' constructs TextEmbedding with CUDA-first providers."""
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
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )

        FastEmbedEmbedder(model_name="m", dim=384, device="cuda")

    assert captured["providers"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)


def test_fastembed_cpu_omits_providers() -> None:
    """device='cpu' (default) constructs without a providers kwarg."""
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
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )

        FastEmbedEmbedder(model_name="m", dim=384)

    assert "providers" not in captured
    sys.modules.pop("pydocs_mcp.extraction.strategies.embedders.fastembed", None)
