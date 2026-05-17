"""Pin the FakeUnitOfWork + InMemory* contract."""
from __future__ import annotations

import pytest

from pydocs_mcp.models import Chunk, ModuleMember, Package, PackageOrigin
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
from pydocs_mcp.storage.protocols import UnitOfWork
from tests._fakes import (
    FakeUnitOfWork,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    InMemoryModuleMemberStore,
    InMemoryPackageStore,
    make_fake_uow_factory,
)


def test_fake_unit_of_work_satisfies_protocol():
    """§14.9 AC #2 — FakeUnitOfWork passes isinstance(_, UnitOfWork)."""
    assert isinstance(FakeUnitOfWork(), UnitOfWork)


@pytest.mark.asyncio
async def test_fake_uow_committed_only_on_explicit_commit():
    """§14.9 AC #6 — committed flips True only after await commit()."""
    uow = FakeUnitOfWork()
    async with uow:
        assert uow.committed is False
        await uow.commit()
    assert uow.committed is True
    assert uow.rolled_back is False


@pytest.mark.asyncio
async def test_fake_uow_rolls_back_when_commit_not_called():
    """§14.9 AC #6 — exit without commit triggers rollback flag."""
    uow = FakeUnitOfWork()
    async with uow:
        pass
    assert uow.committed is False
    assert uow.rolled_back is True


@pytest.mark.asyncio
async def test_fake_uow_rolls_back_on_exception():
    """§14.9 AC #6 — exception in body triggers rollback."""
    uow = FakeUnitOfWork()
    with pytest.raises(ValueError):
        async with uow:
            raise ValueError("boom")
    assert uow.rolled_back is True


def test_fake_uow_attribute_outside_context_raises():
    """§14.9 AC #7 — outside-context repo access raises.

    SqliteUnitOfWork uses ``@property`` so bare attribute access raises
    directly. FakeUnitOfWork can't (``getattr_static`` bypasses
    properties on Python 3.12+, breaking ``isinstance(_, UnitOfWork)``),
    so the fake's repo attrs return a ``_NotEnteredProxy`` that raises
    on any method call — equivalent contract at the point of use.
    """
    uow = FakeUnitOfWork()
    with pytest.raises(UnitOfWorkNotEnteredError):
        # _NotEnteredProxy raises on any attribute / method access.
        # This mirrors what real services / tests would hit when they
        # try to actually use the repo without entering the context.
        uow.packages.get("anything")


@pytest.mark.asyncio
async def test_inmemory_package_store_list_matches_protocol_signature():
    """§14.9 AC #5 — list(filter, limit) signature matches real PackageStore.
    Catches the planned .all() mismatch eng plan-review flagged."""
    store = InMemoryPackageStore()
    result = await store.list(filter=None, limit=200)
    assert result == []


@pytest.mark.asyncio
async def test_make_fake_uow_factory_returns_callable():
    """§9 — helper returns a Callable[[], FakeUnitOfWork]."""
    factory = make_fake_uow_factory()
    assert callable(factory)
    uow = factory()
    assert isinstance(uow, FakeUnitOfWork)


@pytest.mark.asyncio
async def test_make_fake_uow_factory_returned_uows_share_underlying_stores():
    """§9 — each factory call returns a fresh UoW; underlying stores ARE shared."""
    packages = InMemoryPackageStore()
    factory = make_fake_uow_factory(packages=packages)

    uow1 = factory()
    uow2 = factory()
    assert uow1 is not uow2
    assert uow1.packages_store is packages
    assert uow2.packages_store is packages


@pytest.mark.asyncio
async def test_make_fake_uow_factory_is_re_entrance_safe():
    """§6 — each factory call returns an unentered UoW (re-entrance guard cleared)."""
    factory = make_fake_uow_factory()
    async with factory() as uow1:
        pass  # exit normally
    # Second call must succeed despite first having entered+exited.
    async with factory() as uow2:
        assert uow2._entered is True


@pytest.mark.asyncio
async def test_in_memory_package_store_records_calls():
    """§9.1 — InMemoryPackageStore.calls mirrors InMemoryDocumentTreeStore."""
    store = InMemoryPackageStore()
    pkg = Package(
        name="x", version="0", summary="", homepage="",
        dependencies=(), content_hash="", origin=PackageOrigin.DEPENDENCY,
    )
    await store.upsert(pkg)
    await store.get("x")
    assert any(c.method == "upsert" for c in store.calls)
    assert any(c.method == "get" for c in store.calls)


@pytest.mark.asyncio
async def test_in_memory_chunk_store_records_calls():
    """§9.1."""
    store = InMemoryChunkStore()
    chunk = Chunk(text="t", metadata={"package": "x"})
    await store.upsert([chunk])
    assert any(c.method == "upsert" for c in store.calls)


@pytest.mark.asyncio
async def test_in_memory_module_member_store_records_calls():
    """§9.1."""
    store = InMemoryModuleMemberStore()
    m = ModuleMember(metadata={"package": "x", "module": "x.m", "name": "f", "kind": "function"})
    await store.upsert_many([m])
    assert any(c.method == "upsert_many" for c in store.calls)
