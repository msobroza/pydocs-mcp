"""MockEmbedder satisfies Embedder Protocol + returns np.ndarray (AC-27)."""

import numpy as np
import pytest

from pydocs_mcp.storage.protocols import Embedder
from tests._fakes import MockEmbedder


def test_mock_embedder_satisfies_embedder_protocol() -> None:
    assert isinstance(MockEmbedder(dim=4), Embedder)


def test_mock_embedder_dim_field() -> None:
    emb = MockEmbedder(dim=384)
    assert emb.dim == 384


@pytest.mark.asyncio
async def test_mock_embedder_embed_query_returns_ndarray_of_correct_dim() -> None:
    emb = MockEmbedder(dim=8)
    vec = await emb.embed_query("hello world")
    assert isinstance(vec, np.ndarray)
    assert vec.dtype == np.float32
    assert vec.shape == (8,)


@pytest.mark.asyncio
async def test_mock_embedder_is_deterministic_same_input_same_output() -> None:
    emb = MockEmbedder(dim=8)
    v1 = await emb.embed_query("hello world")
    v2 = await emb.embed_query("hello world")
    assert np.array_equal(v1, v2)


@pytest.mark.asyncio
async def test_mock_embedder_different_input_different_output() -> None:
    emb = MockEmbedder(dim=8)
    v1 = await emb.embed_query("alpha")
    v2 = await emb.embed_query("beta")
    assert not np.array_equal(v1, v2)


@pytest.mark.asyncio
async def test_mock_embedder_embed_chunks_returns_one_ndarray_per_text() -> None:
    emb = MockEmbedder(dim=4)
    vecs = await emb.embed_chunks(["x", "y", "z"])
    assert len(vecs) == 3
    assert all(isinstance(v, np.ndarray) and v.shape == (4,) for v in vecs)
    # Each chunk's vector is the same as if embed_query were called on it.
    assert np.array_equal(vecs[0], await emb.embed_query("x"))
