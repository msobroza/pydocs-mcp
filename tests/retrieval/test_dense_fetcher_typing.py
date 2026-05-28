"""Pin the type contract at the dense_fetcher boundary.

After ``is_multi_vector`` narrows the embedder return type,
``query_vec`` must satisfy the ``VectorSearchable.vector_search``
signature without ``# type: ignore``.
"""

from __future__ import annotations

import typing

from pydocs_mcp.models import is_multi_vector


def test_is_multi_vector_is_a_typeguard() -> None:
    """``is_multi_vector`` must be annotated as ``TypeGuard[list[np.ndarray]]``
    so mypy narrows the union after the True branch.

    The False branch then narrows ``Embedding`` back to ``np.ndarray``,
    which is what :meth:`VectorSearchable.vector_search` requires.
    """
    hints = typing.get_type_hints(is_multi_vector)
    return_type = hints.get("return")
    assert return_type is not None
    # ``TypeGuard[T]`` presents as ``typing.TypeGuard[T]`` at runtime via
    # ``typing.get_type_hints``; we just check the origin is ``TypeGuard``.
    origin = typing.get_origin(return_type)
    assert origin is typing.TypeGuard, (
        f"is_multi_vector must return TypeGuard[...] so mypy can narrow "
        f"the union in dense_fetcher; got {return_type!r}"
    )
