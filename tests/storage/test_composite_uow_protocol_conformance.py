"""CompositeUnitOfWork must satisfy the UnitOfWork Protocol so the
factory closures in __main__.py and storage/factories.py can be typed
as ``Callable[[], UnitOfWork]`` without ``# type: ignore``.

The composite routes attribute access through a runtime ``__getattr__``
map, but the ``@runtime_checkable`` Protocol membership check in
:mod:`pydocs_mcp.storage.protocols` does not see attributes only
exposed via ``__getattr__`` — it requires the attributes to be
discoverable via ``hasattr`` at the class or instance level (e.g.,
properties).
"""

from __future__ import annotations

from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.null_multi_vector_store import NullMultiVectorStore
from pydocs_mcp.storage.null_vector_store import NullVectorStore
from pydocs_mcp.storage.protocols import UnitOfWork


class _FakeChild:
    """A child UoW exposing all dispatch attrs eagerly (no
    ``__aenter__``-deferred binding) so ``CompositeUnitOfWork`` can
    build its dispatch map at construction time. Only used by the
    Protocol-conformance test below — production children are
    :class:`SqliteUnitOfWork` + :class:`TurboQuantUnitOfWork`.
    """

    packages = object()
    chunks = object()
    module_members = object()
    trees = object()
    references = object()
    node_scores = object()
    decisions = object()
    vectors = NullVectorStore()
    multi_vectors = NullMultiVectorStore()

    async def __aenter__(self) -> _FakeChild:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def delete_all(self) -> None:
        return None


def test_composite_uow_exposes_dispatch_attrs_at_the_class_level() -> None:
    """The seven dispatch attrs (``packages``, ``chunks``, ``module_members``,
    ``trees``, ``references``, ``vectors``) must be visible on the
    ``CompositeUnitOfWork`` *class* — not only on instances after a
    runtime ``__getattr__`` resolution.

    Why this matters for mypy: when a callable is annotated
    ``Callable[[], CompositeUnitOfWork]`` and consumed by code that
    expects ``Callable[[], UnitOfWork]``, mypy checks the structural
    Protocol membership against the *class*, which doesn't follow
    ``__getattr__`` fallbacks. Explicit ``@property`` declarations
    surface the contract at the class level so the subtype check
    passes without ``# type: ignore`` or explicit casts.
    """
    for attr in (
        "packages",
        "chunks",
        "module_members",
        "trees",
        "references",
        "node_scores",
        "decisions",
        "vectors",
        "multi_vectors",
    ):
        assert hasattr(CompositeUnitOfWork, attr), (
            f"CompositeUnitOfWork class must expose {attr!r} at the class "
            f"level (via a @property) so mypy's structural-subtype check "
            f"against UnitOfWork sees it without walking __getattr__."
        )


def test_composite_uow_passes_runtime_isinstance_check_against_protocol() -> None:
    """``@runtime_checkable`` Protocol membership: ``CompositeUnitOfWork``
    must answer True to ``isinstance(uow, UnitOfWork)`` so factories
    that return ``Callable[[], UnitOfWork]`` accept it without an
    explicit cast.
    """
    uow = CompositeUnitOfWork(_FakeChild())
    assert isinstance(uow, UnitOfWork), (
        "CompositeUnitOfWork must satisfy the @runtime_checkable UnitOfWork "
        "Protocol so composition roots can type uow_factory as "
        "Callable[[], UnitOfWork]."
    )
