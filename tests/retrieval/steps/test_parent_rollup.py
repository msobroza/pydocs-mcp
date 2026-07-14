"""ParentRollupStep — kind-aware sibling→parent rollup (spec 2026-07-14)."""

from __future__ import annotations

import pytest
import yaml

from pydocs_mcp.extraction.model.document_node import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, ChunkList, ModuleMemberList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.retrieval.steps.parent_rollup import (
    _DEFAULT_MIN_COVERAGE,
    _DEFAULT_MIN_COVERAGE_BY_KIND,
    _MIN_SIBLINGS,
    ParentRollupStep,
)
from tests._fakes import (
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)

_PKG = "pkg"
_MOD = "pkg.mod"


# ── fixtures ─────────────────────────────────────────────────────────────


def _node(
    qname: str,
    kind: NodeKind,
    *,
    text: str = "body",
    children: tuple = (),
) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=kind,
        source_path="src.py",
        start_line=1,
        end_line=2,
        text=text,
        content_hash=f"hash-{qname}",
        children=tuple(children),
    )


def _class_tree(n_methods: int, *, root_text: str = "") -> DocumentNode:
    """MODULE root (empty text unless stated) → ClassA `_MOD + '.C'` → n METHODs."""
    methods = tuple(_node(f"{_MOD}.C.m{i}", NodeKind.METHOD) for i in range(n_methods))
    cls = _node(f"{_MOD}.C", NodeKind.CLASS, children=methods)
    return _node(_MOD, NodeKind.MODULE, text=root_text, children=(cls,))


def _chunk(
    qname: str,
    relevance: float | None = None,
    *,
    package: str = _PKG,
    module: str = _MOD,
    text: str | None = None,
) -> Chunk:
    return Chunk(
        text=text or f"text-{qname}",
        relevance=relevance,
        metadata={"package": package, "module": module, "qualified_name": qname},
    )


def _state(items: list[Chunk]) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="q"),
        candidates=ChunkList(items=tuple(items)),
        result=None,
        scratch={},
    )


async def _stores(
    trees: list[DocumentNode] = (),
    chunks: list[Chunk] = (),
    package: str = _PKG,
) -> tuple[InMemoryDocumentTreeStore, InMemoryChunkStore]:
    tree_store = InMemoryDocumentTreeStore()
    chunk_store = InMemoryChunkStore()
    if trees:
        await tree_store.save_many(list(trees), package=package)
    if chunks:
        await chunk_store.upsert(list(chunks))
    return tree_store, chunk_store


def _step(
    tree_store: InMemoryDocumentTreeStore | None = None,
    chunk_store: InMemoryChunkStore | None = None,
    **cfg,
) -> ParentRollupStep:
    return ParentRollupStep(
        uow_factory=make_fake_uow_factory(
            trees=tree_store or InMemoryDocumentTreeStore(),
            chunks=chunk_store or InMemoryChunkStore(),
        ),
        **cfg,
    )


def _qnames(state: RetrieverState) -> list[str]:
    return [c.metadata["qualified_name"] for c in state.candidates.items]


def _ctx() -> BuildContext:
    return BuildContext(uow_factory=make_fake_uow_factory())


# ── AC1–AC5, AC36, AC37: codec, constants, validation ────────────────────


def test_ac1_default_to_dict_is_bare_type() -> None:
    assert _step().to_dict() == {"type": "parent_rollup"}


def test_ac2_round_trip_via_registry_and_no_alias() -> None:
    original = _step(min_coverage=0.6)
    rebuilt = step_registry.build(original.to_dict(), _ctx())
    assert isinstance(rebuilt, ParentRollupStep)
    assert rebuilt.min_coverage == 0.6
    assert "parent_rollup" in step_registry.names()
    assert "rollup" not in step_registry.names()


def test_ac3_from_dict_requires_uow_factory() -> None:
    with pytest.raises(ValueError, match="uow_factory"):
        ParentRollupStep.from_dict({"type": "parent_rollup"}, BuildContext(uow_factory=None))


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_ac4_min_coverage_domain_validated_pre_construction(bad: float) -> None:
    with pytest.raises(ValueError, match=repr(bad)):
        ParentRollupStep.from_dict({"type": "parent_rollup", "min_coverage": bad}, _ctx())


def test_ac5_constants_and_read_only_mapping() -> None:
    assert _DEFAULT_MIN_COVERAGE == 0.5
    assert dict(_DEFAULT_MIN_COVERAGE_BY_KIND) == {
        "class": 0.3,
        "module": 0.6,
        "markdown_heading": 0.5,
    }
    assert _MIN_SIBLINGS == 2
    step = _step()
    with pytest.raises(TypeError):
        step.min_coverage_by_kind["class"] = 0.1  # type: ignore[index]


def test_ac36_custom_mapping_round_trip_and_yaml_dumpable() -> None:
    original = _step(min_coverage_by_kind={"class": 0.2, "function": 0.4})
    data = original.to_dict()
    assert data == {
        "type": "parent_rollup",
        "min_coverage_by_kind": {"class": 0.2, "function": 0.4},
    }
    assert type(data["min_coverage_by_kind"]) is dict
    yaml.safe_dump(data)  # raw mappingproxy would raise RepresenterError
    rebuilt = step_registry.build(data, _ctx())
    assert rebuilt.min_coverage_by_kind == {"class": 0.2, "function": 0.4}
    # A mapping equal to the default table is omitted entirely.
    assert _step(min_coverage_by_kind=dict(_DEFAULT_MIN_COVERAGE_BY_KIND)).to_dict() == {
        "type": "parent_rollup"
    }


def test_ac37_mapping_validation_names_offender_pre_construction() -> None:
    with pytest.raises(ValueError, match="'klass'"):
        ParentRollupStep.from_dict(
            {"type": "parent_rollup", "min_coverage_by_kind": {"klass": 0.3}}, _ctx()
        )
    for bad_value in (1.5, "hot", True):
        with pytest.raises(ValueError, match="'class'"):
            ParentRollupStep.from_dict(
                {"type": "parent_rollup", "min_coverage_by_kind": {"class": bad_value}},
                _ctx(),
            )
    for non_mapping in (0.3, ["class"]):
        with pytest.raises(ValueError, match="must be a mapping"):
            ParentRollupStep.from_dict(
                {"type": "parent_rollup", "min_coverage_by_kind": non_mapping}, _ctx()
            )
    # 0.0 is allowed per-kind (explicit opt-in to maximum eagerness).
    step = ParentRollupStep.from_dict(
        {"type": "parent_rollup", "min_coverage_by_kind": {"class": 0.0}}, _ctx()
    )
    assert step.min_coverage_by_kind == {"class": 0.0}


# ── AC6: guards ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ac6_non_chunklist_and_empty_pass_through_as_identity() -> None:
    step = _step()
    for candidates in (
        None,
        ChunkList(items=()),
        ModuleMemberList(items=()),
    ):
        state = RetrieverState(
            query=SearchQuery(terms="q"), candidates=candidates, result=None, scratch={}
        )
        assert await step.run(state) is state


# ── AC7–AC9, AC24: core gates + happy path ───────────────────────────────


@pytest.mark.asyncio
async def test_ac7_happy_path_collapses_siblings_into_class() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    items = [
        _chunk(f"{_MOD}.C.m0", 0.9),
        _chunk(f"{_MOD}.C.m2", 0.7),
        _chunk("pkg.other.x", 0.6, module="pkg.other"),
        _chunk(f"{_MOD}.C.m1", 0.5),
    ]
    out = await step.run(_state(items))
    # 3/4 hits >= class threshold 0.3, floor met -> parent at the lowest
    # group index (0), siblings gone, non-group candidate order preserved.
    assert _qnames(out) == [f"{_MOD}.C", "pkg.other.x"]
    assert out.candidates.items[0].relevance == 0.9
    # Parent chunk fetched via the three-key filter.
    fetch = next(c for c in cs.calls if c.method == "list")
    assert fetch.payload["filter"] == {
        "package": _PKG,
        "module": _MOD,
        "qualified_name": f"{_MOD}.C",
    }


@pytest.mark.asyncio
async def test_ac8_below_coverage_returns_identity() -> None:
    ts, cs = await _stores(trees=[_class_tree(8)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    state = _state([_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C.m1", 0.8)])
    # 2/8 = 0.25 < 0.3 (class threshold); floor satisfied -> coverage is
    # the failing gate; nothing else rolls up -> identity return.
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_ac9_sibling_floor_blocks_single_hit_and_is_not_configurable() -> None:
    ts, cs = await _stores(trees=[_class_tree(2)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    state = _state([_chunk(f"{_MOD}.C.m0", 0.9)])
    # 1 hit of a 2-method class: coverage 0.5 >= 0.3 but floor fails.
    assert await step.run(state) is state
    # The floor is not configuration: unknown params keys are ignored by
    # the codec, so behavior is unchanged.
    step2 = ParentRollupStep.from_dict(
        {"type": "parent_rollup", "min_children": 1, "min_siblings": 1}, _ctx()
    )
    assert step2.to_dict() == {"type": "parent_rollup"}


@pytest.mark.asyncio
async def test_ac24_coverage_boundary_equality_triggers() -> None:
    ts, cs = await _stores(trees=[_class_tree(10)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    items = [_chunk(f"{_MOD}.C.m{i}", 0.5) for i in (0, 1, 2)]
    out = await step.run(_state(items))
    # 3/10 == 0.30 satisfies >= against the class threshold; `>` fails this.
    assert _qnames(out) == [f"{_MOD}.C"]


# ── AC11–AC13, AC15, AC18, AC20: eligibility + fallbacks ─────────────────


@pytest.mark.asyncio
async def test_ac11_empty_text_parent_never_triggers() -> None:
    methods = tuple(_node(f"{_MOD}.C.m{i}", NodeKind.METHOD) for i in range(2))
    cls = _node(f"{_MOD}.C", NodeKind.CLASS, text="", children=methods)
    root = _node(_MOD, NodeKind.MODULE, text="", children=(cls,))
    # Seed the class chunk row so the ONLY thing blocking a rollup is the
    # empty-text (parent-must-emit) gate — not missing-chunk-row abandonment.
    # 2/2 coverage >= 0.3 and the floor is met, so removing `parent.text.strip()`
    # from _gates_pass would produce a real rollup and fail the identity assert.
    ts, cs = await _stores(trees=[root], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    state = _state([_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C.m1", 0.8)])
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_ac12_missing_parent_chunk_row_abandons_rollup() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)])  # no chunk rows seeded
    step = _step(ts, cs)
    state = _state([_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C.m1", 0.8)])
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_ac13_missing_tree_skips_group_while_other_group_rolls_up() -> None:
    other_methods = tuple(_node(f"pkg.other.D.m{i}", NodeKind.METHOD) for i in range(2))
    other_cls = _node("pkg.other.D", NodeKind.CLASS, children=other_methods)
    other_root = _node("pkg.other", NodeKind.MODULE, text="", children=(other_cls,))
    ts, cs = await _stores(trees=[other_root], chunks=[_chunk("pkg.other.D", module="pkg.other")])
    step = _step(ts, cs)
    items = [
        # Group (pkg, pkg.mod): no tree persisted -> skipped, kept verbatim.
        _chunk(f"{_MOD}.C.m0", 0.9),
        _chunk(f"{_MOD}.C.m1", 0.8),
        # Group (pkg, pkg.other): rolls up (2/2 = 1.0 >= 0.3).
        _chunk("pkg.other.D.m0", 0.7, module="pkg.other"),
        _chunk("pkg.other.D.m1", 0.6, module="pkg.other"),
    ]
    out = await step.run(_state(items))
    assert _qnames(out) == [f"{_MOD}.C.m0", f"{_MOD}.C.m1", "pkg.other.D"]


@pytest.mark.asyncio
async def test_ac13b_drifted_qname_contributes_nothing() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    state = _state(
        [
            _chunk(f"{_MOD}.C.m0", 0.9),
            _chunk(f"{_MOD}.stale_symbol", 0.8),  # matches no tree node
        ]
    )
    # Only 1 real hit -> floor fails; the drifted chunk never counts.
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_ac15_denominator_counts_emitting_children_only() -> None:
    emitting = tuple(_node(f"{_MOD}.C.m{i}", NodeKind.METHOD) for i in range(6))
    silent = tuple(_node(f"{_MOD}.C.s{i}", NodeKind.METHOD, text="") for i in range(2))
    cls = _node(f"{_MOD}.C", NodeKind.CLASS, children=emitting + silent)
    root = _node(_MOD, NodeKind.MODULE, text="", children=(cls,))
    ts, cs = await _stores(trees=[root], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    out = await step.run(_state([_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C.m1", 0.8)]))
    # 2/6 = 0.33 >= 0.3 triggers; counting the empty-text children
    # (2/8 = 0.25) would not.
    assert _qnames(out) == [f"{_MOD}.C"]


@pytest.mark.asyncio
async def test_ac18_missing_metadata_passes_through_verbatim() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    no_qname = Chunk(text="n1", metadata={"package": _PKG, "module": _MOD})
    none_qname = Chunk(
        text="n2", metadata={"package": _PKG, "module": _MOD, "qualified_name": None}
    )
    blank_module = Chunk(
        text="n3", metadata={"package": _PKG, "module": "  ", "qualified_name": f"{_MOD}.C.m3"}
    )
    items = [
        _chunk(f"{_MOD}.C.m0", 0.9),
        no_qname,
        none_qname,
        blank_module,
        _chunk(f"{_MOD}.C.m1", 0.5),
    ]
    out = await step.run(_state(items))
    texts = [c.text for c in out.candidates.items]
    assert texts == [f"text-{_MOD}.C", "n1", "n2", "n3"]


@pytest.mark.asyncio
async def test_ac20_one_tree_load_per_fully_keyed_group() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    items = [
        _chunk(f"{_MOD}.C.m0", 0.9),
        _chunk(f"{_MOD}.C.m1", 0.8),
        _chunk("pkg.other.x", 0.7, module="pkg.other"),
        # Package X's only chunk lacks qualified_name -> no group, no load.
        Chunk(text="nx", metadata={"package": "x", "module": "x.m"}),
    ]
    await step.run(_state(items))
    loads = [c.payload for c in ts.calls if c.method == "load"]
    assert sorted(loads) == [(_PKG, _MOD), (_PKG, "pkg.other")]
