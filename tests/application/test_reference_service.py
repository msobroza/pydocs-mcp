"""ReferenceService tests — single-field uow_factory contract (spec §8.1)."""

from __future__ import annotations

import dataclasses

import pytest

from pydocs_mcp.application.reference_service import (
    ContextNode,
    ImpactNode,
    ReferenceService,
)
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.node_score import NodeScore
from tests._fakes import (
    InMemoryChunkStore,
    InMemoryNodeScoreStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)


def _chunk(qname: str, *, text: str = "") -> Chunk:
    return Chunk(text=text, metadata={"package": "pkg", "qualified_name": qname})


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


def test_reference_service_field_set_is_pinned() -> None:
    """CLAUDE.md §'Creating new application services' — uow_factory plus the
    two cross-repo federation fields (spec 2026-07-11 §3.4a: project_name +
    cross_links, defaulting to the Null store so single-project construction
    is unchanged). Any OTHER field is a contract violation."""
    names = {f.name for f in dataclasses.fields(ReferenceService)}
    assert names == {"uow_factory", "project_name", "cross_links"}


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


# ── inherits() — both senses (bases ∪ subclasses), precision-biased ──


async def _inherits_svc(rows: list[NodeReference]) -> ReferenceService:
    store = InMemoryReferenceStore()
    await store.save_many(rows, package="pkg")
    return ReferenceService(uow_factory=make_fake_uow_factory(references=store))


@pytest.mark.asyncio
async def test_inherits_returns_from_side_base_edges():
    """Sense 1 (BASES): from-side INHERITS edges of the target — even when
    ``to_name`` is the bare source-text name and the edge is unresolved
    (the pre-fix exact ``to_name`` probe returned nothing here)."""
    row = _ref(
        from_node_id="pkg.mod.Child",
        to_name="BetaBase",
        to_node_id=None,
        kind=ReferenceKind.INHERITS,
    )
    svc = await _inherits_svc([row])
    assert await svc.inherits("pkg", "pkg.mod.Child") == (row,)


@pytest.mark.asyncio
async def test_inherits_returns_resolved_subclass_edges():
    """Sense 2 (SUBCLASSES): edges resolved INTO the target match on
    ``to_node_id`` even when ``to_name`` stores the bare source-text name."""
    row = _ref(
        from_node_id="pkg.mod.Child",
        to_name="Base",
        to_node_id="pkg.mod.Base",
        kind=ReferenceKind.INHERITS,
    )
    svc = await _inherits_svc([row])
    assert await svc.inherits("pkg", "pkg.mod.Base") == (row,)


@pytest.mark.asyncio
async def test_inherits_exact_dotted_to_name_match_included():
    """An UNRESOLVED edge whose ``to_name`` is the exact fully-dotted target
    still counts as a subclass edge (covers dotted source text)."""
    row = _ref(
        from_node_id="pkg.other.Child",
        to_name="pkg.mod.Base",
        to_node_id=None,
        kind=ReferenceKind.INHERITS,
    )
    svc = await _inherits_svc([row])
    assert await svc.inherits("pkg", "pkg.mod.Base") == (row,)


@pytest.mark.asyncio
async def test_inherits_never_matches_bare_last_segment():
    """Precision bias: an unresolved bare ``to_name`` that merely suffixes
    the target's last segment is NOT a subclass match."""
    row = _ref(
        from_node_id="pkg.other.Child",
        to_name="Base",  # bare — could be anyone's Base
        to_node_id=None,
        kind=ReferenceKind.INHERITS,
    )
    svc = await _inherits_svc([row])
    assert await svc.inherits("pkg", "pkg.mod.Base") == ()


@pytest.mark.asyncio
async def test_inherits_dedups_row_matched_by_both_subclass_queries():
    """A resolved edge whose ``to_name`` ALSO equals the dotted target is
    found by both subclass probes — returned exactly once."""
    row = _ref(
        from_node_id="pkg.mod.Child",
        to_name="pkg.mod.Base",
        to_node_id="pkg.mod.Base",
        kind=ReferenceKind.INHERITS,
    )
    svc = await _inherits_svc([row])
    assert await svc.inherits("pkg", "pkg.mod.Base") == (row,)


@pytest.mark.asyncio
async def test_inherits_excludes_to_name_match_resolved_elsewhere():
    """A ``to_name`` match the resolver pinned to a DIFFERENT qname is
    excluded — the resolver's verdict wins (precision bias)."""
    row = _ref(
        from_node_id="pkg.other.Child",
        to_name="pkg.mod.Base",
        to_node_id="vendored.pkg.mod.Base",
        kind=ReferenceKind.INHERITS,
    )
    svc = await _inherits_svc([row])
    assert await svc.inherits("pkg", "pkg.mod.Base") == ()


@pytest.mark.asyncio
async def test_inherits_ignores_non_inherits_kinds():
    """CALLS/IMPORTS edges touching the target never leak into inherits."""
    rows = [
        _ref(from_node_id="pkg.mod.Base", to_name="fn", to_node_id="pkg.mod.fn"),
        _ref(
            from_node_id="pkg.mod.user",
            to_name="pkg.mod.Base",
            to_node_id="pkg.mod.Base",
            kind=ReferenceKind.IMPORTS,
        ),
    ]
    svc = await _inherits_svc(rows)
    assert await svc.inherits("pkg", "pkg.mod.Base") == ()


@pytest.mark.asyncio
async def test_inherits_bases_ordered_before_subclasses():
    """Combined return keeps bases first (they survive a limit slice)."""
    base_edge = _ref(
        from_node_id="pkg.mod.Mid",
        to_name="Root",
        to_node_id="pkg.mod.Root",
        kind=ReferenceKind.INHERITS,
    )
    sub_edge = _ref(
        from_node_id="pkg.mod.Leaf",
        to_name="Mid",
        to_node_id="pkg.mod.Mid",
        kind=ReferenceKind.INHERITS,
    )
    svc = await _inherits_svc([sub_edge, base_edge])
    assert await svc.inherits("pkg", "pkg.mod.Mid") == (base_edge, sub_edge)


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


# ── context() — smart-context dependency closure (lookup(show="context")) ──


def _ctx_svc(refs, *, chunks=None, node_scores=None):
    return ReferenceService(
        uow_factory=make_fake_uow_factory(references=refs, chunks=chunks, node_scores=node_scores)
    )


@pytest.mark.asyncio
async def test_context_seed_is_focus_hop0_first():
    refs = InMemoryReferenceStore()
    await refs.save_many([_edge("S", "A")], package="pkg")
    chunks = InMemoryChunkStore()
    await chunks.upsert([_chunk("S", text="def s(): a()"), _chunk("A", text="def a(): pass")])
    out = await _ctx_svc(refs, chunks=chunks).context("pkg", "S", max_depth=2, limit=10)
    assert isinstance(out[0], ContextNode)
    assert (out[0].qualified_name, out[0].hop) == ("S", 0)  # seed = focus, first
    assert out[0].source_text == "def s(): a()"
    assert [n.qualified_name for n in out] == ["S", "A"]
    assert any(c.method == "find_transitive_callees" for c in refs.calls)


@pytest.mark.asyncio
async def test_context_ranks_callees_by_pagerank():
    refs = InMemoryReferenceStore()
    await refs.save_many([_edge("S", "A"), _edge("S", "B")], package="pkg")  # both hop 1
    chunks = InMemoryChunkStore()
    await chunks.upsert([_chunk("S"), _chunk("A"), _chunk("B")])
    nss = InMemoryNodeScoreStore()
    await nss.upsert(
        [_score("A", pagerank=0.1, in_degree=1), _score("B", pagerank=0.9, in_degree=1)]
    )
    out = await _ctx_svc(refs, chunks=chunks, node_scores=nss).context(
        "pkg", "S", max_depth=1, limit=10
    )
    assert [n.qualified_name for n in out] == ["S", "B", "A"]  # seed, then B (higher pagerank)


@pytest.mark.asyncio
async def test_context_limit_caps_total_nodes():
    refs = InMemoryReferenceStore()
    await refs.save_many([_edge("S", "A"), _edge("S", "B"), _edge("S", "C")], package="pkg")
    chunks = InMemoryChunkStore()
    await chunks.upsert([_chunk(q) for q in ("S", "A", "B", "C")])
    nss = InMemoryNodeScoreStore()
    await nss.upsert(
        [
            _score("A", pagerank=0.3, in_degree=0),
            _score("B", pagerank=0.9, in_degree=0),
            _score("C", pagerank=0.6, in_degree=0),
        ]
    )
    out = await _ctx_svc(refs, chunks=chunks, node_scores=nss).context(
        "pkg", "S", max_depth=1, limit=2
    )
    assert [n.qualified_name for n in out] == ["S", "B"]  # seed + top-1 callee


@pytest.mark.asyncio
async def test_context_populates_source_text_from_chunk():
    refs = InMemoryReferenceStore()
    await refs.save_many([_edge("S", "A")], package="pkg")
    chunks = InMemoryChunkStore()
    await chunks.upsert([_chunk("S"), _chunk("A", text="def a(x):\n    return x")])
    out = await _ctx_svc(refs, chunks=chunks).context("pkg", "S", max_depth=1, limit=10)
    a = next(n for n in out if n.qualified_name == "A")
    assert a.source_text == "def a(x):\n    return x"


@pytest.mark.asyncio
async def test_context_fanin_fallback_without_scores():
    # No node_scores; A has higher fan-in than B → A ranks first among callees.
    refs = InMemoryReferenceStore()
    await refs.save_many([_edge("S", "A"), _edge("S", "B"), _edge("X", "A")], package="pkg")
    chunks = InMemoryChunkStore()
    await chunks.upsert([_chunk(q) for q in ("S", "A", "B")])
    out = await _ctx_svc(refs, chunks=chunks).context("pkg", "S", max_depth=1, limit=10)
    assert [n.qualified_name for n in out] == ["S", "A", "B"]
    a = next(n for n in out if n.qualified_name == "A")
    assert a.in_degree == 2  # S + X


@pytest.mark.asyncio
async def test_context_empty_closure_returns_just_seed():
    refs = InMemoryReferenceStore()
    await refs.save_many([_edge("X", "Y")], package="pkg")  # unrelated to S
    chunks = InMemoryChunkStore()
    await chunks.upsert([_chunk("S", text="def s(): pass")])
    out = await _ctx_svc(refs, chunks=chunks).context("pkg", "S", max_depth=2, limit=10)
    assert [n.qualified_name for n in out] == ["S"]


@pytest.mark.asyncio
async def test_context_missing_chunk_yields_empty_source():
    refs = InMemoryReferenceStore()
    await refs.save_many([_edge("S", "A")], package="pkg")
    chunks = InMemoryChunkStore()
    await chunks.upsert([_chunk("S", text="def s(): a()")])  # no chunk for A
    out = await _ctx_svc(refs, chunks=chunks).context("pkg", "S", max_depth=1, limit=10)
    a = next(n for n in out if n.qualified_name == "A")
    assert a.source_text == ""
