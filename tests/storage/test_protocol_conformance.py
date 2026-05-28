"""Runtime Protocol conformance gates (spec C1 + C4 + I3 + S15 + I21).

Pins that the SQLite adapters and the retrieval-side
:class:`PerCallConnectionProvider` continue to satisfy their respective
@runtime_checkable Protocols. Catches drift the moment a Protocol gains
a method or an adapter loses one — much earlier than the integration
tests would.
"""

from __future__ import annotations

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.retrieval.protocols import ConnectionProvider as RetrievalConnectionProvider
from pydocs_mcp.storage.null_vector_store import NullVectorStore
from pydocs_mcp.storage.protocols import (
    FilterAdapter,
    ModuleMemberStore,
    ReferenceStore,
    UnitOfWork,
)
from pydocs_mcp.storage.sqlite import (
    SqliteFilterAdapter,
    SqliteModuleMemberRepository,
    SqliteReferenceStore,
    SqliteUnitOfWork,
)


def test_per_call_connection_provider_conforms(tmp_path):
    """:class:`PerCallConnectionProvider` satisfies the retrieval Protocol.

    Also verifies the new ``acquire_sync`` surface (spec C4) is callable.
    """
    db = tmp_path / "x.db"
    db.touch()
    provider = PerCallConnectionProvider(cache_path=db)
    assert isinstance(provider, RetrievalConnectionProvider)
    assert hasattr(provider, "acquire_sync")
    assert callable(provider.acquire_sync)


def test_sqlite_filter_adapter_conforms():
    """The public ``SqliteFilterAdapter`` satisfies the tightened C5 Protocol."""
    adapter = SqliteFilterAdapter()
    assert isinstance(adapter, FilterAdapter)


def test_sqlite_reference_repository_conforms(tmp_path):
    """SqliteReferenceStore satisfies ReferenceStore — including C1's resolve_unresolved."""
    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    repo = SqliteReferenceStore(provider=provider)
    assert isinstance(repo, ReferenceStore)
    assert hasattr(repo, "resolve_unresolved") and callable(repo.resolve_unresolved)
    assert hasattr(repo, "delete_all") and callable(repo.delete_all)


def test_sqlite_module_member_repository_conforms(tmp_path):
    """SqliteModuleMemberRepository satisfies ModuleMemberStore (spec I21).

    Pins the Protocol type used in retrieval.serialization.BuildContext so
    swapping the concrete class for a different ModuleMemberStore impl is
    a pure protocol-level change.
    """
    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    repo = SqliteModuleMemberRepository(provider=provider)
    assert isinstance(repo, ModuleMemberStore)
    # Task 1.6: every entity-store Protocol declares delete_all symmetrically.
    assert hasattr(repo, "delete_all") and callable(repo.delete_all)


def test_sqlite_package_repository_delete_all(tmp_path):
    """SqlitePackageRepository.delete_all satisfies PackageStore.delete_all
    (Task 1.6 Protocol symmetry)."""
    from pydocs_mcp.storage.protocols import PackageStore
    from pydocs_mcp.storage.sqlite import SqlitePackageRepository

    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    repo = SqlitePackageRepository(provider=provider)
    assert isinstance(repo, PackageStore)
    assert hasattr(repo, "delete_all") and callable(repo.delete_all)


def test_sqlite_chunk_repository_delete_all(tmp_path):
    """SqliteChunkRepository.delete_all satisfies ChunkStore.delete_all
    (Task 1.6 Protocol symmetry)."""
    from pydocs_mcp.storage.protocols import ChunkStore
    from pydocs_mcp.storage.sqlite import SqliteChunkRepository

    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    repo = SqliteChunkRepository(provider=provider)
    assert isinstance(repo, ChunkStore)
    assert hasattr(repo, "delete_all") and callable(repo.delete_all)


def test_sqlite_unit_of_work_conforms(tmp_path):
    """SqliteUnitOfWork satisfies UnitOfWork — including I3's delete_all + S15's vectors.

    ``isinstance(uow, UnitOfWork)`` is asserted INSIDE ``async with uow:``
    because the @property repo accessors raise
    ``UnitOfWorkNotEnteredError`` outside the context (which trips the
    Python 3.11 :class:`runtime_checkable` ``getattr``-based check).
    The new ``vectors`` + ``delete_all`` shape is async, so the test
    itself runs under ``asyncio``.
    """
    import asyncio

    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    uow = SqliteUnitOfWork(provider=provider)
    # Always-present attributes (spec S15 + I3 are validated outside the
    # transaction context — they don't depend on `_entered`).
    assert hasattr(uow, "delete_all") and callable(uow.delete_all)
    assert hasattr(uow, "vectors")
    # SQLite-only deployments default ``vectors`` to NullVectorStore.
    assert isinstance(uow.vectors, NullVectorStore)

    async def _enter_and_check():
        async with uow as opened:
            assert isinstance(opened, UnitOfWork)

    asyncio.run(_enter_and_check())
