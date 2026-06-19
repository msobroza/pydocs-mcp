"""GraphExpandStep — dense-seeded reference-graph expansion (embedding-centric).

Covers serialization (defaults / round-trip / strict-gate / clamping), the
safety contract (empty or graph-barren input returns state unchanged), and the
expansion+merge behaviour (caller/callee discovery, decayed scoring, cycle
guard, unresolved-edge skip, kind filtering, max-merge with dense).
"""

from __future__ import annotations

import pytest

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.retrieval.steps.graph_expand import (
    _DEFAULT_DECAY,
    _DEFAULT_MAX_DEPTH,
    _DEFAULT_TOP_S,
    GraphExpandStep,
)
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import (
    InMemoryChunkStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)

# ── builders ──────────────────────────────────────────────────────────────


def _chunk(qname: str, relevance: float, *, text: str = "body", cid: int | None = None) -> Chunk:
    return Chunk(
        text=text,
        id=cid,
        relevance=relevance,
        metadata={"qualified_name": qname, "package": "pkg"},
    )


def _ref(
    from_node_id: str,
    to_name: str,
    to_node_id: str | None,
    kind: ReferenceKind = ReferenceKind.CALLS,
) -> NodeReference:
    return NodeReference(
        from_package="pkg",
        from_node_id=from_node_id,
        to_name=to_name,
        to_node_id=to_node_id,
        kind=kind,
    )


def _factory(refs: list[NodeReference], chunks: list[Chunk]):
    return make_fake_uow_factory(
        references=InMemoryReferenceStore(by_package={"pkg": list(refs)}),
        chunks=InMemoryChunkStore(by_package={"pkg": list(chunks)}),
    )


def _state(items: list[Chunk]) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="q", max_results=5),
        candidates=ChunkList(items=tuple(items)),
        result=None,
        scratch={},
    )


def _ctx(uow_factory=None) -> BuildContext:
    return BuildContext(uow_factory=uow_factory or make_fake_uow_factory())


def _by_qname(state: RetrieverState) -> dict[str, Chunk]:
    assert isinstance(state.candidates, ChunkList)
    return {c.metadata["qualified_name"]: c for c in state.candidates.items}


# ── serialization ─────────────────────────────────────────────────────────


def test_to_dict_defaults_is_type_only() -> None:
    d = GraphExpandStep(uow_factory=make_fake_uow_factory()).to_dict()
    assert d == {"type": "graph_expand"}


def test_to_dict_includes_non_defaults() -> None:
    d = GraphExpandStep(
        uow_factory=make_fake_uow_factory(),
        top_s=5,
        max_depth=2,
        decay=0.7,
        directions=("callers",),
        kinds=("calls",),
        neighbors_per_seed=3,
    ).to_dict()
    assert d == {
        "type": "graph_expand",
        "top_s": 5,
        "max_depth": 2,
        "decay": 0.7,
        "directions": ["callers"],
        "kinds": ["calls"],
        "neighbors_per_seed": 3,
    }


def test_from_dict_round_trip_via_registry() -> None:
    original = GraphExpandStep(
        uow_factory=make_fake_uow_factory(),
        top_s=4,
        max_depth=2,
        decay=0.25,
        directions=("callers",),
    )
    rebuilt = step_registry.build(original.to_dict(), _ctx())
    assert isinstance(rebuilt, GraphExpandStep)
    assert (rebuilt.top_s, rebuilt.max_depth, rebuilt.decay, rebuilt.directions) == (
        4,
        2,
        0.25,
        ("callers",),
    )


def test_from_dict_requires_uow_factory() -> None:
    with pytest.raises(ValueError, match="uow_factory"):
        GraphExpandStep.from_dict({"type": "graph_expand"}, BuildContext(uow_factory=None))


@pytest.mark.parametrize(("given", "clamped"), [(5, 2), (0, 1), (-3, 1), (2, 2)])
def test_from_dict_clamps_max_depth(given: int, clamped: int) -> None:
    step = GraphExpandStep.from_dict({"type": "graph_expand", "max_depth": given}, _ctx())
    assert step.max_depth == clamped


def test_from_dict_rejects_invalid_direction() -> None:
    with pytest.raises(ValueError, match="directions"):
        GraphExpandStep.from_dict(
            {"type": "graph_expand", "directions": ["callers", "siblings"]}, _ctx()
        )


def test_defaults() -> None:
    step = GraphExpandStep(uow_factory=make_fake_uow_factory())
    assert (step.top_s, step.max_depth, step.decay) == (
        _DEFAULT_TOP_S,
        _DEFAULT_MAX_DEPTH,
        _DEFAULT_DECAY,
    )


# ── safety contract ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_none_candidates_unchanged() -> None:
    step = GraphExpandStep(uow_factory=_factory([], []))
    state = RetrieverState(query=SearchQuery(terms="q"), candidates=None, result=None, scratch={})
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_run_empty_candidates_unchanged() -> None:
    step = GraphExpandStep(uow_factory=_factory([], []))
    state = _state([])
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_run_no_qualified_name_seeds_unchanged() -> None:
    step = GraphExpandStep(uow_factory=_factory([_ref("pkg.caller", "pkg.s", "pkg.s")], []))
    # candidate carries no qualified_name → cannot seed the graph.
    bare = Chunk(text="x", relevance=0.9, metadata={"package": "pkg"})
    state = _state([bare])
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_run_no_neighbours_unchanged() -> None:
    # Seed has a qname but the graph has no matching edges → unchanged.
    step = GraphExpandStep(uow_factory=_factory([], []))
    state = _state([_chunk("pkg.s", 0.9)])
    assert await step.run(state) is state


# ── expansion + scoring ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_surfaces_caller_with_decayed_score() -> None:
    refs = [_ref("pkg.caller", "pkg.target", "pkg.target", ReferenceKind.CALLS)]
    chunks = [_chunk("pkg.caller", 0.0, text="def caller(): target()")]
    step = GraphExpandStep(uow_factory=_factory(refs, chunks))
    out = await step.run(_state([_chunk("pkg.target", 0.9)]))
    by = _by_qname(out)
    assert "pkg.caller" in by
    assert by["pkg.caller"].relevance == pytest.approx(0.9 * _DEFAULT_DECAY)
    assert by["pkg.target"].relevance == pytest.approx(0.9)  # seed untouched


@pytest.mark.asyncio
async def test_run_surfaces_callee() -> None:
    refs = [_ref("pkg.seed", "pkg.helper", "pkg.helper", ReferenceKind.CALLS)]
    chunks = [_chunk("pkg.helper", 0.0)]
    step = GraphExpandStep(uow_factory=_factory(refs, chunks))
    out = await step.run(_state([_chunk("pkg.seed", 0.8)]))
    by = _by_qname(out)
    assert by["pkg.helper"].relevance == pytest.approx(0.8 * _DEFAULT_DECAY)


@pytest.mark.asyncio
async def test_run_inherits_override_caller() -> None:
    # An overriding subclass method appears as an INHERITS caller of the base.
    refs = [_ref("pkg.Sub.method", "pkg.Base.method", "pkg.Base.method", ReferenceKind.INHERITS)]
    chunks = [_chunk("pkg.Sub.method", 0.0)]
    step = GraphExpandStep(uow_factory=_factory(refs, chunks))
    out = await step.run(_state([_chunk("pkg.Base.method", 1.0)]))
    assert "pkg.Sub.method" in _by_qname(out)


@pytest.mark.asyncio
async def test_run_cycle_guard_terminates_no_dup() -> None:
    # A ↔ B mutual calls; depth 2 must terminate and list B exactly once.
    refs = [
        _ref("pkg.A", "pkg.B", "pkg.B", ReferenceKind.CALLS),
        _ref("pkg.B", "pkg.A", "pkg.A", ReferenceKind.CALLS),
    ]
    chunks = [_chunk("pkg.B", 0.0)]
    step = GraphExpandStep(uow_factory=_factory(refs, chunks), max_depth=2)
    out = await step.run(_state([_chunk("pkg.A", 1.0)]))
    qnames = [c.metadata["qualified_name"] for c in out.candidates.items]
    assert qnames.count("pkg.B") == 1
    assert _by_qname(out)["pkg.B"].relevance == pytest.approx(0.5)  # hop-1 score


@pytest.mark.asyncio
async def test_run_skips_unresolved_callee() -> None:
    # Callee edge with to_node_id=None (target outside the index) yields nothing.
    refs = [_ref("pkg.seed", "os.path.join", None, ReferenceKind.CALLS)]
    step = GraphExpandStep(uow_factory=_factory(refs, []))
    state = _state([_chunk("pkg.seed", 0.9)])
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_run_ignores_kind_not_configured() -> None:
    # MENTIONS edge is excluded by the default kinds=(calls, inherits).
    refs = [_ref("pkg.caller", "pkg.seed", "pkg.seed", ReferenceKind.MENTIONS)]
    chunks = [_chunk("pkg.caller", 0.0)]
    step = GraphExpandStep(uow_factory=_factory(refs, chunks))
    state = _state([_chunk("pkg.seed", 0.9)])
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_run_merge_keeps_max_of_dense_and_graph() -> None:
    # N is both a weak dense hit (0.3) and a caller of the seed (graph 0.45).
    # top_s=1 keeps N OUT of the seed window so the graph can boost it — seeds
    # themselves are excluded from discovery (graph adds recall, it doesn't
    # re-rank the top dense hits).
    refs = [_ref("pkg.N", "pkg.seed", "pkg.seed", ReferenceKind.CALLS)]
    chunks = [_chunk("pkg.N", 0.0)]
    step = GraphExpandStep(uow_factory=_factory(refs, chunks), top_s=1)
    out = await step.run(_state([_chunk("pkg.seed", 0.9), _chunk("pkg.N", 0.3)]))
    by = _by_qname(out)
    assert by["pkg.N"].relevance == pytest.approx(0.9 * _DEFAULT_DECAY)  # 0.45 > 0.3
    # ranking: seed (0.9) then N (0.45)
    assert [c.metadata["qualified_name"] for c in out.candidates.items][:2] == ["pkg.seed", "pkg.N"]


@pytest.mark.asyncio
async def test_run_dense_only_when_neighbour_has_no_chunk() -> None:
    # Graph finds a caller, but no chunk exists for it (stdlib/dep) → no add.
    refs = [_ref("pkg.caller", "pkg.seed", "pkg.seed", ReferenceKind.CALLS)]
    step = GraphExpandStep(uow_factory=_factory(refs, []))  # empty chunk store
    state = _state([_chunk("pkg.seed", 0.9)])
    out = await step.run(state)
    assert [c.metadata["qualified_name"] for c in out.candidates.items] == ["pkg.seed"]
