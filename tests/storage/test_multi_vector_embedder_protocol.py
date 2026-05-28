"""MultiVectorEmbedder Protocol smoke (spec AC-1)."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from pydocs_mcp.storage.protocols import MultiVectorEmbedder


def test_protocol_is_runtime_checkable() -> None:
    class _Fake:
        dim: int = 128
        model_name: str = "fake"
        async def embed_query(self, text: str):
            return [np.zeros((128,), dtype=np.float32)]
        async def embed_chunks(self, texts: Sequence[str]):
            return tuple([np.zeros((128,), dtype=np.float32)] for _ in texts)

    assert isinstance(_Fake(), MultiVectorEmbedder)
