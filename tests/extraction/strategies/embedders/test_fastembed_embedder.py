"""FastEmbedEmbedder construction (AC-13, AC-16)."""
import sys
from unittest.mock import MagicMock, patch


def test_fastembedembedder_construction_with_mocked_fastembed() -> None:
    """When fastembed is mocked, FastEmbedEmbedder constructs OK."""
    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = MagicMock()

    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.fastembed", None,
    )
    with patch.dict(sys.modules, {"fastembed": mock_fastembed}):
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )
        emb = FastEmbedEmbedder(model_name="BAAI/bge-small-en-v1.5", dim=384)
        assert emb.dim == 384
        assert emb.model_name == "BAAI/bge-small-en-v1.5"

    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.fastembed", None,
    )
