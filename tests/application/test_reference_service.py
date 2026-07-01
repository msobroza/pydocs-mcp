"""ReferenceService tests — single-field uow_factory contract (spec §8.1)."""

from __future__ import annotations

import dataclasses

import pytest

from pydocs_mcp.application.reference_service import ImpactNode, ReferenceService
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.node_score import NodeScore
from tests._fakes import (
    InMemoryNodeScoreStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)


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


def _edge(frm: str, to: str, kind: ReferenceKind = ReferenceKind.CALLS) -> NodeReference:
    """A resolved caller edge ``frm`` → ``to``."""
    return _ref(from_node_id=frm, to_name=to, to_node_id=to, kind=kind)


def _score(qname: str, *, pagerank: float, in_degree: int) -> NodeScore:
    return NodeScore(
        package="pkg", qualified_name=qname, in_degree=in_degree, pagerank=pagerank, community=0
    )


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


# ── impact() — ranked blast-radius (lookup(show="impact")) ──


@pytest.mark.asyncio
async def test_impact_reads_via_find_transitive_callers():
    store = InMemoryReferenceStore()
    await store.save_many([_edge("A", "T")], package="pkg")
    svc = ReferenceService(uow_factory=make_fake_uow_factory(references=store))
    out = await svc.impact("pkg", "T", max_depth=2, limit=10)
    assert isinstance(out, tuple)
    assert [n.qualified_name for n in out] == ["A"]
    assert isinstance(out[0], ImpactNode)
    assert any(c.method == "find_transitive_callers" for c in store.calls)


@pytest.mark.asyncio
async def test_impact_ranks_by_pagerank_when_scores_present():
    store = InMemoryReferenceStore()
    await store.save_many([_edge("A", "T"), _edge("B", "T")], package="pkg")  # both hop 1
    nss = InMemoryNodeScoreStore()
    await nss.upsert(
        [_score("A", pagerank=0.1, in_degree=1), _score("B", pagerank=0.9, in_degree=1)]
    )
    svc = ReferenceService(uow_factory=make_fake_uow_factory(references=store, node_scores=nss))
    out = await svc.impact("pkg", "T", max_depth=1, limit=10)
    assert [n.qualified_name for n in out] == ["B", "A"]  # pagerank desc within same hop
    assert all(n.has_scores for n in out)
    assert out[0].pagerank == 0.9


@pytest.mark.asyncio
async def test_impact_falls_back_to_fanin_without_scores():
    # A has 2 real callers, B has 1 → fan-in ranks A before B (same hop, no node_scores).
    store = InMemoryReferenceStore()
    await store.save_many(
        [_edge("A", "T"), _edge("B", "T"), _edge("X", "A"), _edge("Y", "A"), _edge("Z", "B")],
        package="pkg",
    )
    svc = ReferenceService(uow_factory=make_fake_uow_factory(references=store))  # empty node_scores
    out = await svc.impact("pkg", "T", max_depth=1, limit=10)
    assert [n.qualified_name for n in out] == ["A", "B"]  # fan-in desc
    assert all(not n.has_scores for n in out)
    assert out[0].in_degree == 2


@pytest.mark.asyncio
async def test_impact_hop_dominates_centrality():
    # A hop 1 (pagerank 0.01), B hop 2 (pagerank 0.99) → A still ranks first.
    store = InMemoryReferenceStore()
    await store.save_many([_edge("A", "T"), _edge("B", "A")], package="pkg")
    nss = InMemoryNodeScoreStore()
    await nss.upsert(
        [_score("A", pagerank=0.01, in_degree=1), _score("B", pagerank=0.99, in_degree=1)]
    )
    svc = ReferenceService(uow_factory=make_fake_uow_factory(references=store, node_scores=nss))
    out = await svc.impact("pkg", "T", max_depth=2, limit=10)
    assert [n.qualified_name for n in out] == ["A", "B"]


@pytest.mark.asyncio
async def test_impact_limit_slices_after_ranking():
    store = InMemoryReferenceStore()
    await store.save_many([_edge("A", "T"), _edge("B", "T"), _edge("C", "T")], package="pkg")
    nss = InMemoryNodeScoreStore()
    await nss.upsert(
        [
            _score("A", pagerank=0.3, in_degree=0),
            _score("B", pagerank=0.9, in_degree=0),
            _score("C", pagerank=0.6, in_degree=0),
        ]
    )
    svc = ReferenceService(uow_factory=make_fake_uow_factory(references=store, node_scores=nss))
    out = await svc.impact("pkg", "T", max_depth=1, limit=2)
    assert [n.qualified_name for n in out] == ["B", "C"]  # top-2 by pagerank, AFTER ranking


@pytest.mark.asyncio
async def test_impact_empty_when_no_callers():
    store = InMemoryReferenceStore()
    await store.save_many([_edge("A", "B")], package="pkg")
    svc = ReferenceService(uow_factory=make_fake_uow_factory(references=store))
    out = await svc.impact("pkg", "T", max_depth=3, limit=10)
    assert out == ()
