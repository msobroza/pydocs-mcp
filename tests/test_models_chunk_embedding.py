"""Chunk.embedding field is additive + np.ndarray-typed (spec §5.1 + AC-2)."""
import dataclasses

import numpy as np
import pytest

from pydocs_mcp.models import Chunk


def test_chunk_constructed_without_embedding_defaults_none() -> None:
    c = Chunk(text="hello")
    assert c.embedding is None


def test_chunk_accepts_single_vector_ndarray() -> None:
    vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    c = Chunk(text="hello", embedding=vec)
    assert isinstance(c.embedding, np.ndarray)
    assert np.array_equal(c.embedding, vec)


def test_chunk_accepts_multi_vector_list_of_ndarrays() -> None:
    multi = [
        np.array([0.1, 0.2], dtype=np.float32),
        np.array([0.3, 0.4], dtype=np.float32),
    ]
    c = Chunk(text="hello", embedding=multi)
    assert isinstance(c.embedding, list)
    assert len(c.embedding) == 2
    assert np.array_equal(c.embedding[0], multi[0])


def test_chunk_remains_frozen() -> None:
    c = Chunk(text="hello", embedding=np.array([0.1, 0.2], dtype=np.float32))
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.embedding = np.array([0.3, 0.4], dtype=np.float32)  # type: ignore[misc]
