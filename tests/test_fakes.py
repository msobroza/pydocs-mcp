"""Pin the FakeUnitOfWork + InMemory* contract."""
from __future__ import annotations

import pytest

from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
from pydocs_mcp.storage.protocols import UnitOfWork
from tests._fakes import (
    FakeUnitOfWork,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    InMemoryModuleMemberStore,
    InMemoryPackageStore,
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
    """§14.9 AC #7 — mirrors SqliteUnitOfWork contract."""
    uow = FakeUnitOfWork()
    with pytest.raises(UnitOfWorkNotEnteredError):
        _ = uow.packages


@pytest.mark.asyncio
async def test_inmemory_package_store_list_matches_protocol_signature():
    """§14.9 AC #5 — list(filter, limit) signature matches real PackageStore.
    Catches the planned .all() mismatch eng plan-review flagged."""
    store = InMemoryPackageStore()
    result = await store.list(filter=None, limit=200)
    assert result == []
