"""CompositeUnitOfWork dispatch + attribute proxying + rollback (AC-9, AC-10)."""
from dataclasses import dataclass, field
from typing import Any

import pytest

from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork


@dataclass
class FakeUoW:
    name: str
    fail_commit: bool = False
    commits: list[str] = field(default_factory=list)
    rollbacks: list[str] = field(default_factory=list)
    _attrs: dict[str, Any] = field(default_factory=dict)

    async def __aenter__(self) -> "FakeUoW":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def commit(self) -> None:
        self.commits.append(self.name)
        if self.fail_commit:
            raise RuntimeError(f"{self.name} commit blew up")

    async def rollback(self) -> None:
        self.rollbacks.append(self.name)

    def __getattr__(self, name: str) -> Any:
        if name in self._attrs:
            return self._attrs[name]
        raise AttributeError(name)


@pytest.mark.asyncio
async def test_commit_dispatches_to_all_children() -> None:
    a = FakeUoW("a")
    b = FakeUoW("b")
    async with CompositeUnitOfWork([a, b]) as uow:
        await uow.commit()
    assert a.commits == ["a"]
    assert b.commits == ["b"]


@pytest.mark.asyncio
async def test_rollback_on_partial_commit_failure() -> None:
    a = FakeUoW("a")
    b = FakeUoW("b", fail_commit=True)
    with pytest.raises(RuntimeError, match="b commit blew up"):
        async with CompositeUnitOfWork([a, b]) as uow:
            await uow.commit()
    assert a.commits == ["a"]
    assert b.commits == ["b"]
    assert a.rollbacks == ["a"]


@pytest.mark.asyncio
async def test_attribute_proxying_to_owning_child() -> None:
    a = FakeUoW("a")
    a._attrs["packages"] = "packages_store_from_a"
    b = FakeUoW("b")
    b._attrs["vectors"] = "vectors_store_from_b"
    async with CompositeUnitOfWork([a, b]) as uow:
        assert uow.packages == "packages_store_from_a"
        assert uow.vectors == "vectors_store_from_b"


@pytest.mark.asyncio
async def test_ambiguous_attribute_raises_at_access() -> None:
    a = FakeUoW("a")
    a._attrs["chunks"] = "from_a"
    b = FakeUoW("b")
    b._attrs["chunks"] = "from_b"
    async with CompositeUnitOfWork([a, b]) as uow:
        with pytest.raises(AttributeError, match="ambiguous"):
            _ = uow.chunks


@pytest.mark.asyncio
async def test_unknown_attribute_raises_clear_error() -> None:
    a = FakeUoW("a")
    async with CompositeUnitOfWork([a]) as uow:
        with pytest.raises(AttributeError, match="nonexistent"):
            _ = uow.nonexistent


@pytest.mark.asyncio
async def test_empty_children_raises_at_construction() -> None:
    with pytest.raises(ValueError, match="at least one"):
        CompositeUnitOfWork([])
