"""Embedding type aliases align with FastEmbed convention (spec §5.1, AC-1).

Spec S12: the legacy ``Vector = np.ndarray`` alias was dropped — call
sites now reference ``np.ndarray`` directly. The tests below still
validate the alias-free invariants (``MultiVector`` is a list,
``is_multi_vector`` discriminates correctly, ``SparseEmbedding`` is a
forward-compat Protocol).
"""
import numpy as np

from pydocs_mcp.models import (
    Embedding,
    MultiVector,
    SparseEmbedding,
    is_multi_vector,
)


def test_embedding_union_includes_np_ndarray() -> None:
    """``np.ndarray`` is the single-vector half of the ``Embedding`` union.

    Replaces the legacy ``Vector is np.ndarray`` alias assertion (spec S12).
    """
    import typing
    args = typing.get_args(Embedding)
    assert np.ndarray in args


def test_multi_vector_alias_accepts_list_of_ndarray() -> None:
    # Pure runtime check — MultiVector is `list[np.ndarray]`.
    mv: MultiVector = [np.array([1.0, 2.0]), np.array([3.0, 4.0])]
    assert isinstance(mv, list)
    assert all(isinstance(v, np.ndarray) for v in mv)


def test_is_multi_vector_single_vector_false() -> None:
    single: np.ndarray = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert is_multi_vector(single) is False


def test_is_multi_vector_multi_vector_true() -> None:
    multi: MultiVector = [
        np.array([1.0, 2.0], dtype=np.float32),
        np.array([3.0, 4.0], dtype=np.float32),
    ]
    assert is_multi_vector(multi) is True


def test_is_multi_vector_empty_ndarray_false() -> None:
    # Empty single vector is still a single np.ndarray, not a MultiVector.
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
    """Sentinel: this PR's Embedding union stays ``np.ndarray | MultiVector``.
    Adding SparseEmbedding is a future PR's job."""
    # typing.get_args on a union type alias returns the union members.
    import typing
    args = typing.get_args(Embedding)
    assert SparseEmbedding not in args
