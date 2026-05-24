"""FastEmbedEmbedder construction + import-guard (AC-13, AC-16)."""
import sys
from unittest.mock import MagicMock, patch

import pytest


def test_import_without_fastembed_raises_optionaldepmissing() -> None:
    # Hide fastembed from sys.modules + sys.meta_path so the import fails.
    saved = sys.modules.pop("fastembed", None)

    class _BlockFastembed:
        def find_module(self, name, path=None):
            if name == "fastembed" or name.startswith("fastembed."):
                return self
            return None

        def load_module(self, name):
            raise ImportError(f"No module named {name!r}")

    finder = _BlockFastembed()
    sys.meta_path.insert(0, finder)
    # Also remove our module from cache so the import re-runs.
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.fastembed", None,
    )
    try:
        from pydocs_mcp.extraction.strategies.embedders import (
            OptionalDepMissing,
        )
        with pytest.raises(OptionalDepMissing, match=r"pip install pydocs-mcp\[fastembed\]"):
            from pydocs_mcp.extraction.strategies.embedders.fastembed import (  # noqa: F401
                FastEmbedEmbedder,
            )
    finally:
        sys.meta_path.remove(finder)
        if saved is not None:
            sys.modules["fastembed"] = saved
        sys.modules.pop(
            "pydocs_mcp.extraction.strategies.embedders.fastembed", None,
        )


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
