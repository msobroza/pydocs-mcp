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
    async with CompositeUnitOfWork(a, b) as uow:
        await uow.commit()
    assert a.commits == ["a"]
    assert b.commits == ["b"]


@pytest.mark.asyncio
async def test_rollback_on_partial_commit_failure() -> None:
    a = FakeUoW("a")
    b = FakeUoW("b", fail_commit=True)
    with pytest.raises(RuntimeError, match="b commit blew up"):
        async with CompositeUnitOfWork(a, b) as uow:
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
    async with CompositeUnitOfWork(a, b) as uow:
        assert uow.packages == "packages_store_from_a"
        assert uow.vectors == "vectors_store_from_b"


def test_ambiguous_attribute_raises_at_construction() -> None:
    # Spec I4 / S26: ambiguous attrs are now caught up-front rather
    # than on first access, so misconfigured composition roots fail
    # at wire-up time. FakeUoW exposes attrs lazily via _attrs but
    # `hasattr` still triggers the dict lookup, so the construction
    # scan picks the overlap up.
    a = FakeUoW("a")
    a._attrs["chunks"] = "from_a"
    b = FakeUoW("b")
    b._attrs["chunks"] = "from_b"
    with pytest.raises(ValueError, match="ambiguous"):
        CompositeUnitOfWork(a, b)


@pytest.mark.asyncio
async def test_unknown_attribute_raises_clear_error() -> None:
    a = FakeUoW("a")
    async with CompositeUnitOfWork(a) as uow:
        with pytest.raises(AttributeError, match="nonexistent"):
            _ = uow.nonexistent


@pytest.mark.asyncio
async def test_empty_children_raises_at_construction() -> None:
    with pytest.raises(ValueError, match="at least one"):
        CompositeUnitOfWork()


class _FakeChunksA:
    pass


class _FakeChunksB:
    pass


class _FakeUow1:
    chunks = _FakeChunksA()


class _FakeUow2:
    chunks = _FakeChunksB()  # ambiguous overlap with _FakeUow1


def test_composite_uow_rejects_ambiguous_children() -> None:
    # Spec I4 / S26: overlapping attributes across children must be
    # detected at construction, not on first access. Avoids the
    # foot-gun where a misconfigured composition root only blows up
    # the first time someone reaches uow.chunks deep inside a
    # request handler.
    with pytest.raises(ValueError, match="ambiguous"):
        CompositeUnitOfWork(_FakeUow1(), _FakeUow2())


def test_composite_uow_star_args_signature() -> None:
    # Spec S26: *children signature — no list wrapping required.
    # Each child's repositories become directly addressable via the
    # composite without per-call hasattr scanning.
    class _A:
        chunks = object()

    class _B:
        packages = object()

    a, b = _A(), _B()
    uow = CompositeUnitOfWork(a, b)
    assert uow.chunks is a.chunks
    assert uow.packages is b.packages
