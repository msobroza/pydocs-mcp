"""Embedder + ResultFuser Protocols exist + are runtime_checkable (spec §5.2)."""
import numpy as np

from pydocs_mcp.storage.protocols import Embedder, ResultFuser


def test_embedder_protocol_exposes_required_attrs() -> None:
    assert hasattr(Embedder, "dim")
    assert hasattr(Embedder, "model_name")
    assert hasattr(Embedder, "embed_query")
    assert hasattr(Embedder, "embed_chunks")


def test_resultfuser_protocol_exposes_fuse() -> None:
    assert hasattr(ResultFuser, "fuse")


def test_embedder_is_runtime_checkable() -> None:
    class Stub:
        dim = 4
        model_name = "stub"
        async def embed_query(self, text: str): return np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
        async def embed_chunks(self, texts): return tuple(np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32) for _ in texts)

    assert isinstance(Stub(), Embedder)


def test_resultfuser_is_runtime_checkable() -> None:
    class Stub:
        async def fuse(self, ranked_lists, *, limit): return ()

    assert isinstance(Stub(), ResultFuser)
