"""Embedding type aliases align with FastEmbed convention (spec §5.1, AC-1)."""
import numpy as np

from pydocs_mcp.models import (
    Embedding,
    MultiVector,
    SparseEmbedding,
    Vector,
    is_multi_vector,
)


def test_vector_is_np_ndarray_alias() -> None:
    assert Vector is np.ndarray


def test_multi_vector_alias_accepts_list_of_ndarray() -> None:
    # Pure runtime check — MultiVector is `list[np.ndarray]`.
    mv: MultiVector = [np.array([1.0, 2.0]), np.array([3.0, 4.0])]
    assert isinstance(mv, list)
    assert all(isinstance(v, np.ndarray) for v in mv)


def test_is_multi_vector_single_vector_false() -> None:
    single: Vector = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert is_multi_vector(single) is False


def test_is_multi_vector_multi_vector_true() -> None:
    multi: MultiVector = [
        np.array([1.0, 2.0], dtype=np.float32),
        np.array([3.0, 4.0], dtype=np.float32),
    ]
    assert is_multi_vector(multi) is True


def test_is_multi_vector_empty_ndarray_false() -> None:
    # Empty single vector is still a Vector, not a MultiVector.
    assert is_multi_vector(np.array([], dtype=np.float32)) is False


def test_sparse_embedding_protocol_runtime_checkable() -> None:
    """SparseEmbedding is a runtime_checkable Protocol matching FastEmbed's
    shape. Not in the Embedding union this PR — just defined for forward
    compatibility."""
    class _Stub:
        indices = np.array([0, 5, 9], dtype=np.uint32)
        values = np.array([0.5, 0.7, 0.2], dtype=np.float32)

    assert isinstance(_Stub(), SparseEmbedding)


def test_sparse_embedding_NOT_in_embedding_union_yet() -> None:
    """Sentinel: this PR's Embedding union stays Vector | MultiVector.
    Adding SparseEmbedding is a future PR's job."""
    # typing.get_args on a union type alias returns the union members.
    import typing
    args = typing.get_args(Embedding)
    assert SparseEmbedding not in args
