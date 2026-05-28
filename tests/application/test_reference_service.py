"""ReferenceService tests — single-field uow_factory contract (spec §8.1)."""

from __future__ import annotations

import dataclasses

import pytest

from pydocs_mcp.application.reference_service import ReferenceService
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import InMemoryReferenceStore, make_fake_uow_factory


def _ref(**kw) -> NodeReference:
    base = dict(
        from_package="pkg",
        from_node_id="pkg.mod.fn",
        to_name="x",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    base.update(kw)
    return NodeReference(**base)


def test_reference_service_only_has_uow_factory_field() -> None:
    """CLAUDE.md §'Creating new application services' — single field rule."""
    names = {f.name for f in dataclasses.fields(ReferenceService)}
    assert names == {"uow_factory"}


def test_reference_service_is_frozen_slotted_dataclass() -> None:
    svc = ReferenceService(uow_factory=make_fake_uow_factory())
    with pytest.raises(dataclasses.FrozenInstanceError):
        svc.uow_factory = lambda: None  # type: ignore[misc]
    assert not hasattr(svc, "__dict__")


@pytest.mark.asyncio
async def test_callers_opens_uow_and_reads_through_uow_references():
    """spec §8.1 — callers() opens UoW + reads via uow.references.find_callers."""
    store = InMemoryReferenceStore()
    await store.save_many(
        [_ref(to_name="t", to_node_id="t", kind=ReferenceKind.CALLS)],
        package="pkg",
    )
    svc = ReferenceService(uow_factory=make_fake_uow_factory(references=store))
    out = await svc.callers("pkg", "t")
    assert isinstance(out, tuple)
    assert len(out) == 1
    assert any(c.method == "find_callers" for c in store.calls)


@pytest.mark.asyncio
async def test_callees_opens_uow_and_reads_through_uow_references():
    store = InMemoryReferenceStore()
    await store.save_many(
        [_ref(from_node_id="pkg.a", to_name="x", to_node_id="x")],
        package="pkg",
    )
    svc = ReferenceService(uow_factory=make_fake_uow_factory(references=store))
    out = await svc.callees("pkg", "pkg.a")
    assert len(out) == 1
    assert any(c.method == "find_callees" for c in store.calls)


@pytest.mark.asyncio
async def test_find_by_name_with_optional_kind_filter():
    store = InMemoryReferenceStore()
    await store.save_many(
        [
            _ref(to_name="os.path.join", kind=ReferenceKind.CALLS),
            _ref(to_name="os.path.join", kind=ReferenceKind.IMPORTS, from_node_id="pkg.b"),
        ],
        package="pkg",
    )
    svc = ReferenceService(uow_factory=make_fake_uow_factory(references=store))
    all_hits = await svc.find_by_name("os.path.join")
    assert len(all_hits) == 2
    calls_only = await svc.find_by_name(
        "os.path.join",
        kind=ReferenceKind.CALLS,
    )
    assert {r.kind for r in calls_only} == {ReferenceKind.CALLS}


@pytest.mark.asyncio
async def test_callers_does_not_call_commit():
    """Read paths use the __aexit__ rollback safety net — no commit call."""
    store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=store)
    # Wrap the factory to track committed flag.
    fakes = []

    def tracking_factory():
        uow = factory()
        fakes.append(uow)
        return uow

    svc = ReferenceService(uow_factory=tracking_factory)
    await svc.callers("pkg", "any")
    # Reads never commit — the FakeUnitOfWork's `committed` flag stays False.
    assert all(not f.committed for f in fakes)
    # And `rolled_back` is True because __aexit__ treats no-commit as rollback.
    assert all(f.rolled_back for f in fakes)
