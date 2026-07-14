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


@pytest.mark.asyncio
async def test_all_ungroupable_candidates_return_identity() -> None:
    # Non-empty ChunkList where every chunk lacks a groupable key
    # (no qualified_name) -> _group_candidates yields nothing -> identity
    # return. Distinct from the AC6 empty/non-ChunkList guards and from
    # AC18's mixed valid+invalid list.
    step = _step()
    state = _state(
        [
            Chunk(text="a", metadata={"package": _PKG, "module": _MOD}),
            Chunk(text="b", metadata={"package": _PKG, "module": _MOD}),
        ]
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


# ── AC10, AC38, AC40: kind-resolved thresholds ───────────────────────────


def _module_tree(n_functions: int, *, root_text: str = "module docstring") -> DocumentNode:
    functions = tuple(_node(f"{_MOD}.f{i}", NodeKind.FUNCTION) for i in range(n_functions))
    return _node(_MOD, NodeKind.MODULE, text=root_text, children=functions)


@pytest.mark.asyncio
async def test_ac10_class_and_module_diverge_at_identical_coverage() -> None:
    # (a) class tree: 3 of 10 methods -> collapses (0.3 >= class 0.3).
    ts, cs = await _stores(trees=[_class_tree(10)], chunks=[_chunk(f"{_MOD}.C")])
    out = await _step(ts, cs).run(_state([_chunk(f"{_MOD}.C.m{i}", 0.5) for i in (0, 1, 2)]))
    assert _qnames(out) == [f"{_MOD}.C"]

    # (b) module tree: 3 of 10 functions -> NO rollup (0.3 < module 0.6).
    ts2, cs2 = await _stores(trees=[_module_tree(10)], chunks=[_chunk(_MOD)])
    state = _state([_chunk(f"{_MOD}.f{i}", 0.5) for i in (0, 1, 2)])
    assert await _step(ts2, cs2).run(state) is state

    # (c) module tree: 6 of 10 -> collapses into the module's own chunk,
    # fetched via qualified_name == module (6/10 >= 0.6).
    ts3, cs3 = await _stores(trees=[_module_tree(10)], chunks=[_chunk(_MOD)])
    out3 = await _step(ts3, cs3).run(_state([_chunk(f"{_MOD}.f{i}", 0.5) for i in range(6)]))
    assert _qnames(out3) == [_MOD]
    fetch = next(c for c in cs3.calls if c.method == "list")
    assert fetch.payload["filter"]["qualified_name"] == _MOD


@pytest.mark.asyncio
async def test_ac38_fallback_for_unmapped_kinds_and_replace_wholesale() -> None:
    def _function_tree(n_examples: int) -> DocumentNode:
        examples = tuple(_node(f"{_MOD}.f#ex{i}", NodeKind.CODE_EXAMPLE) for i in range(n_examples))
        fn = _node(f"{_MOD}.f", NodeKind.FUNCTION, children=examples)
        return _node(_MOD, NodeKind.MODULE, text="", children=(fn,))

    # (a) "function" absent from the default mapping -> fallback 0.5:
    # 2/4 = 0.5 >= 0.5 -> rollup.
    ts, cs = await _stores(trees=[_function_tree(4)], chunks=[_chunk(f"{_MOD}.f")])
    out = await _step(ts, cs).run(
        _state([_chunk(f"{_MOD}.f#ex0", 0.9), _chunk(f"{_MOD}.f#ex1", 0.8)])
    )
    assert _qnames(out) == [f"{_MOD}.f"]

    # (b) 2/5 = 0.4 < 0.5 -> no rollup.
    ts2, cs2 = await _stores(trees=[_function_tree(5)], chunks=[_chunk(f"{_MOD}.f")])
    state = _state([_chunk(f"{_MOD}.f#ex0", 0.9), _chunk(f"{_MOD}.f#ex1", 0.8)])
    assert await _step(ts2, cs2).run(state) is state

    # (c) an explicit {"function": 0.3} entry flips (b) to a rollup.
    ts3, cs3 = await _stores(trees=[_function_tree(5)], chunks=[_chunk(f"{_MOD}.f")])
    out3 = await _step(ts3, cs3, min_coverage_by_kind={"function": 0.3}).run(
        _state([_chunk(f"{_MOD}.f#ex0", 0.9), _chunk(f"{_MOD}.f#ex1", 0.8)])
    )
    assert _qnames(out3) == [f"{_MOD}.f"]

    # (d) replace-wholesale: under {"function": 0.3}, "module" is absent so
    # the 0.5 fallback governs the root — 5/10 = 0.5 >= 0.5 rolls up (a
    # per-key merge with the default module: 0.6 would block it).
    ts4, cs4 = await _stores(trees=[_module_tree(10)], chunks=[_chunk(_MOD)])
    out4 = await _step(ts4, cs4, min_coverage_by_kind={"function": 0.3}).run(
        _state([_chunk(f"{_MOD}.f{i}", 0.5) for i in range(5)])
    )
    assert _qnames(out4) == [_MOD]


@pytest.mark.asyncio
async def test_ac40_markdown_heading_and_whole_doc_thresholds() -> None:
    _DOC = "pkg.guide.md"

    def _md_tree(n_examples: int, *, root_text: str = "") -> DocumentNode:
        examples = tuple(
            _node(f"{_DOC}#h1-ex{i}", NodeKind.CODE_EXAMPLE) for i in range(n_examples)
        )
        heading = _node(f"{_DOC}#h1", NodeKind.MARKDOWN_HEADING, children=examples)
        return _node(_DOC, NodeKind.MODULE, text=root_text, children=(heading,))

    def _md_chunk(qname: str, relevance: float | None = None) -> Chunk:
        return _chunk(qname, relevance, module=_DOC)

    # (a) heading rollup: 2/4 = 0.5 >= markdown_heading 0.5 (equality pin).
    ts, cs = await _stores(trees=[_md_tree(4)], chunks=[_md_chunk(f"{_DOC}#h1")])
    out = await _step(ts, cs).run(
        _state([_md_chunk(f"{_DOC}#h1-ex0", 0.9), _md_chunk(f"{_DOC}#h1-ex1", 0.8)])
    )
    assert _qnames(out) == [f"{_DOC}#h1"]

    # (b) 2/5 = 0.4 < 0.5 -> no rollup.
    ts2, cs2 = await _stores(trees=[_md_tree(5)], chunks=[_md_chunk(f"{_DOC}#h1")])
    state = _state([_md_chunk(f"{_DOC}#h1-ex0", 0.9), _md_chunk(f"{_DOC}#h1-ex1", 0.8)])
    assert await _step(ts2, cs2).run(state) is state

    # (c) whole-doc rollup gated by the MODULE entry: preamble-bearing root
    # with 5 headings, 3 co-retrieved -> 3/5 = 0.6 >= module 0.6 (equality
    # pin on the module entry); 2/5 = 0.4 -> no rollup.
    headings = tuple(_node(f"{_DOC}#h{i}", NodeKind.MARKDOWN_HEADING) for i in range(5))
    root = _node(_DOC, NodeKind.MODULE, text="preamble prose", children=headings)
    ts3, cs3 = await _stores(trees=[root], chunks=[_md_chunk(_DOC)])
    out3 = await _step(ts3, cs3).run(_state([_md_chunk(f"{_DOC}#h{i}", 0.5) for i in (0, 1, 2)]))
    assert _qnames(out3) == [_DOC]
    ts4, cs4 = await _stores(trees=[root], chunks=[_md_chunk(_DOC)])
    state4 = _state([_md_chunk(f"{_DOC}#h0", 0.5), _md_chunk(f"{_DOC}#h1", 0.5)])
    assert await _step(ts4, cs4).run(state4) is state4


# ── AC14, AC16, AC25–AC28, AC35: replacement semantics ───────────────────


@pytest.mark.asyncio
async def test_ac14_parent_already_in_results_is_self_folded_and_reused() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    in_list_parent = _chunk(f"{_MOD}.C", 0.6)
    out = await step.run(
        _state([_chunk(f"{_MOD}.C.m0", 0.9), in_list_parent, _chunk(f"{_MOD}.C.m1", 0.8)])
    )
    assert _qnames(out) == [f"{_MOD}.C"]
    # In-list object reused: relevance folded to max of the whole group.
    assert out.candidates.items[0].relevance == 0.9
    # No DB fetch for the parent (reuse path).
    assert not [c for c in cs.calls if c.method == "list"]


@pytest.mark.asyncio
async def test_ac16_duplicate_sibling_qnames_count_once_and_all_collapse() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    state = _state(
        [
            _chunk(f"{_MOD}.C.m0", 0.9),
            _chunk(f"{_MOD}.C.m0", 0.85),  # duplicate qname (AST redefinition)
            _chunk(f"{_MOD}.C.m1", 0.8),
        ]
    )
    out = await step.run(state)
    # m0 counts ONCE for coverage (2 distinct hits of 4 = 0.5 >= 0.3);
    # both m0 bearer indices collapse.
    assert _qnames(out) == [f"{_MOD}.C"]


@pytest.mark.asyncio
async def test_ac25_mixed_none_relevance_folds_max_without_typeerror() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    out = await _step(ts, cs).run(
        _state(
            [
                _chunk(f"{_MOD}.C.m0", None),
                _chunk(f"{_MOD}.C.m1", 0.4),
                _chunk(f"{_MOD}.C.m2", None),
            ]
        )
    )
    assert out.candidates.items[0].relevance == 0.4


@pytest.mark.asyncio
async def test_ac26_all_none_relevance_rolls_up_with_none() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    out = await _step(ts, cs).run(
        _state([_chunk(f"{_MOD}.C.m0", None), _chunk(f"{_MOD}.C.m1", None)])
    )
    assert _qnames(out) == [f"{_MOD}.C"]
    assert out.candidates.items[0].relevance is None


@pytest.mark.asyncio
async def test_ac27_interleaved_groups_rebuild_by_index() -> None:
    a_methods = tuple(_node(f"{_MOD}.A.m{i}", NodeKind.METHOD) for i in range(2))
    b_methods = tuple(_node(f"{_MOD}.B.m{i}", NodeKind.METHOD) for i in range(2))
    cls_a = _node(f"{_MOD}.A", NodeKind.CLASS, children=a_methods)
    cls_b = _node(f"{_MOD}.B", NodeKind.CLASS, children=b_methods)
    root = _node(_MOD, NodeKind.MODULE, text="", children=(cls_a, cls_b))
    ts, cs = await _stores(trees=[root], chunks=[_chunk(f"{_MOD}.A"), _chunk(f"{_MOD}.B")])
    out = await _step(ts, cs).run(
        _state(
            [
                _chunk(f"{_MOD}.A.m0", 0.9),  # group A at {0, 3}
                _chunk(f"{_MOD}.B.m0", 0.8),  # group B at {1, 2}
                _chunk(f"{_MOD}.B.m1", 0.7),
                _chunk(f"{_MOD}.A.m1", 0.6),
            ]
        )
    )
    # Index-by-index rebuild: parentA at index 0, parentB at index 1.
    assert _qnames(out) == [f"{_MOD}.A", f"{_MOD}.B"]


@pytest.mark.asyncio
async def test_ac28_parent_candidacy_never_counts_toward_gates() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    state = _state([_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C", 0.8)])
    # hits = 1 (< _MIN_SIBLINGS): the parent's own candidacy is not a hit —
    # a parent-inclusive count of 2 would wrongly trigger (2/4 = 0.5 >= 0.3).
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_ac35_retriever_name_rule_on_both_paths() -> None:
    # Fetch path: the DB row carries retriever_name=None (row_to_chunk
    # never sets it); the emitted parent keeps None.
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    out = await _step(ts, cs).run(
        _state([_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C.m1", 0.8)])
    )
    assert out.candidates.items[0].retriever_name is None

    # Reuse path: the in-list candidate's retriever_name is kept untouched.
    ts2, cs2 = await _stores(trees=[_class_tree(4)])
    in_list_parent = Chunk(
        text="parent",
        relevance=0.5,
        retriever_name="dense",
        metadata={"package": _PKG, "module": _MOD, "qualified_name": f"{_MOD}.C"},
    )
    out2 = await _step(ts2, cs2).run(
        _state([_chunk(f"{_MOD}.C.m0", 0.9), in_list_parent, _chunk(f"{_MOD}.C.m1", 0.8)])
    )
    assert out2.candidates.items[0].retriever_name == "dense"


# ── AC17, AC19, AC29–AC33: claims, cascade, dedup ────────────────────────


def _nested_tree(n_outer_siblings: int) -> DocumentNode:
    """mod(text='') → outer → {inner, s0..s(n-1)}, inner → {leaf0, leaf1}."""
    leaves = tuple(_node(f"{_MOD}.O.I.leaf{i}", NodeKind.METHOD) for i in range(2))
    inner = _node(f"{_MOD}.O.I", NodeKind.CLASS, children=leaves)
    siblings = tuple(_node(f"{_MOD}.O.s{i}", NodeKind.METHOD) for i in range(n_outer_siblings))
    outer = _node(f"{_MOD}.O", NodeKind.CLASS, children=(inner, *siblings))
    return _node(_MOD, NodeKind.MODULE, text="", children=(outer,))


@pytest.mark.asyncio
async def test_ac17_no_cascade_rolled_up_inner_is_not_an_outer_hit() -> None:
    ts, cs = await _stores(
        trees=[_nested_tree(7)], chunks=[_chunk(f"{_MOD}.O.I"), _chunk(f"{_MOD}.O")]
    )
    out = await _step(ts, cs).run(
        _state(
            [
                _chunk(f"{_MOD}.O.I.leaf0", 0.9),
                _chunk(f"{_MOD}.O.I.leaf1", 0.8),
                _chunk(f"{_MOD}.O.s0", 0.7),
                _chunk(f"{_MOD}.O.s1", 0.6),
            ]
        )
    )
    # inner triggers (2/2). outer's legal hits are {s0, s1} = 2 of 8
    # emitting children -> 0.25 < 0.3 -> no trigger. Counting the rolled-up
    # inner chunk as a hit (3/8 = 0.375) would wrongly trigger.
    assert _qnames(out) == [f"{_MOD}.O.I", f"{_MOD}.O.s0", f"{_MOD}.O.s1"]


@pytest.mark.asyncio
async def test_ac19_scratch_never_mutated_and_new_state_via_replace() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    scratch: dict[str, object] = {"upstream.key": "v"}
    state = RetrieverState(
        query=SearchQuery(terms="q"),
        candidates=ChunkList(items=(_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C.m1", 0.8))),
        result=None,
        scratch=scratch,
    )
    out = await step.run(state)
    assert out is not state  # rollup happened -> new state via replace
    assert state.scratch == {"upstream.key": "v"}
    assert out.scratch is state.scratch  # replace() keeps the reference; no writes


@pytest.mark.asyncio
async def test_ac29_atomic_claims_with_candidate_parent_self_fold() -> None:
    ts, cs = await _stores(
        trees=[_nested_tree(2)], chunks=[_chunk(f"{_MOD}.O.I"), _chunk(f"{_MOD}.O")]
    )
    out = await _step(ts, cs).run(
        _state(
            [
                _chunk(f"{_MOD}.O.I", 0.9),  # inner itself is a candidate
                _chunk(f"{_MOD}.O.I.leaf0", 0.8),
                _chunk(f"{_MOD}.O.I.leaf1", 0.7),
                _chunk(f"{_MOD}.O.s0", 0.6),
                _chunk(f"{_MOD}.O.s1", 0.5),
            ]
        )
    )
    # inner triggers on its leaves and self-folds its own index; outer's
    # re-check excludes inner (claimed) leaving {s0, s1} = 2/3 >= 0.3 ->
    # outer triggers on its direct children. One chunk per original index.
    assert _qnames(out) == [f"{_MOD}.O.I", f"{_MOD}.O"]
    assert out.candidates.items[0].relevance == 0.9
    assert out.candidates.items[1].relevance == 0.6


@pytest.mark.asyncio
async def test_ac30_abandonment_releases_claim_for_shallower_parent() -> None:
    # inner's chunk row deliberately absent; outer's row present.
    ts, cs = await _stores(trees=[_nested_tree(2)], chunks=[_chunk(f"{_MOD}.O")])
    out = await _step(ts, cs).run(
        _state(
            [
                _chunk(f"{_MOD}.O.I.leaf0", 0.9),
                _chunk(f"{_MOD}.O.I.leaf1", 0.8),
                _chunk(f"{_MOD}.O.s0", 0.7),
                _chunk(f"{_MOD}.O.s1", 0.6),
            ]
        )
    )
    # inner triggers first (post-order) but its fetch misses -> abandoned,
    # leaves kept. outer triggers on {s0, s1} (2/3 >= 0.3) and collapses.
    assert _qnames(out) == [f"{_MOD}.O.I.leaf0", f"{_MOD}.O.I.leaf1", f"{_MOD}.O"]
    fetches = [c.payload["filter"]["qualified_name"] for c in cs.calls if c.method == "list"]
    assert f"{_MOD}.O.I" in fetches  # the recorded miss


@pytest.mark.asyncio
async def test_ac31_duplicate_parent_qnames_merge_into_one_emission() -> None:
    # Same class qname defined twice (TYPE_CHECKING redefinition), disjoint
    # children.
    first = _node(
        f"{_MOD}.C",
        NodeKind.CLASS,
        children=(
            _node(f"{_MOD}.C.a0", NodeKind.METHOD),
            _node(f"{_MOD}.C.a1", NodeKind.METHOD),
        ),
    )
    second = DocumentNode(
        node_id=f"{_MOD}.C",
        qualified_name=f"{_MOD}.C",
        title="C",
        kind=NodeKind.CLASS,
        source_path="src.py",
        start_line=10,
        end_line=20,
        text="body2",
        content_hash="hash-C2",
        children=(
            _node(f"{_MOD}.C.b0", NodeKind.METHOD),
            _node(f"{_MOD}.C.b1", NodeKind.METHOD),
        ),
    )
    root = _node(_MOD, NodeKind.MODULE, text="", children=(first, second))
    ts, cs = await _stores(trees=[root], chunks=[_chunk(f"{_MOD}.C")])
    out = await _step(ts, cs).run(
        _state(
            [
                _chunk(f"{_MOD}.C.a0", 0.9),
                _chunk(f"{_MOD}.C.b0", 0.8),
                _chunk(f"{_MOD}.C.a1", 0.7),
                _chunk(f"{_MOD}.C.b1", 0.6),
            ]
        )
    )
    # Both nodes trigger; merged: single emission at the lowest combined
    # index, exactly one fetch for the shared qname.
    assert _qnames(out) == [f"{_MOD}.C"]
    fetches = [c for c in cs.calls if c.method == "list"]
    assert len(fetches) == 1


@pytest.mark.asyncio
async def test_ac32_module_key_drift_is_a_pinned_noop() -> None:
    # Tree persisted under module _MOD; candidates carry a divergent module
    # override -> group (pkg, "pkg.mod.override") load misses -> kept.
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    state = _state(
        [
            _chunk(f"{_MOD}.C.m0", 0.9, module="pkg.mod.override"),
            _chunk(f"{_MOD}.C.m1", 0.8, module="pkg.mod.override"),
        ]
    )
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_ac33_cross_group_dedup_keeps_lowest_index_occurrence() -> None:
    # The same class indexed under two (package, module) groups with an
    # IDENTICAL (qualified_name, content_hash) pair: one group's methods
    # roll up; the other group's identical class chunk survives as a
    # candidate -> the text appears exactly once, at the lowest index.
    dup_parent_candidate = Chunk(
        text=f"text-{_MOD}.C",
        relevance=0.95,
        metadata={"package": _PKG, "module": "pkg.dual", "qualified_name": f"{_MOD}.C"},
    )
    # content_hash auto-computes over package+module+title+text (so the two
    # copies would differ on module alone); force identity by constructing
    # the row with the SAME content_hash — the dedup key is (qname, hash).
    parent_row = Chunk(
        text=f"text-{_MOD}.C",
        metadata={"package": _PKG, "module": _MOD, "qualified_name": f"{_MOD}.C"},
        content_hash=dup_parent_candidate.content_hash,
    )
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[parent_row])
    out = await _step(ts, cs).run(
        _state(
            [
                dup_parent_candidate,  # index 0 — survives (lowest occurrence)
                _chunk(f"{_MOD}.C.m0", 0.9),
                _chunk(f"{_MOD}.C.m1", 0.8),
            ]
        )
    )
    # The emitted parent (from the rollup at index 1) duplicates the
    # surviving candidate at index 0 -> dropped; text appears once.
    texts = [c.text for c in out.candidates.items]
    assert texts == [f"text-{_MOD}.C"]
    assert out.candidates.items[0].relevance == 0.95


@pytest.mark.asyncio
async def test_ac17b_claimed_inner_not_counted_as_outer_hit() -> None:
    # inner (O.I) is itself a candidate AND a direct child of outer (O).
    # After inner rolls up its leaves it self-folds its own index; the
    # claimed-index gate in _hit_qnames must then EXCLUDE inner from
    # outer's hit set. With the gate, outer sees only {s0} = 1 < floor 2
    # -> no outer rollup. Without it, outer would see {inner, s0} = 2/4
    # = 0.5 >= 0.3 and wrongly trigger. Pins the no-cascade claimed-index
    # exclusion that AC17 alone does not (AC17's inner is not a candidate).
    leaves = tuple(_node(f"{_MOD}.O.I.leaf{i}", NodeKind.METHOD) for i in range(2))
    inner = _node(f"{_MOD}.O.I", NodeKind.CLASS, children=leaves)
    siblings = tuple(_node(f"{_MOD}.O.s{i}", NodeKind.METHOD) for i in range(3))
    outer = _node(f"{_MOD}.O", NodeKind.CLASS, children=(inner, *siblings))
    root = _node(_MOD, NodeKind.MODULE, text="", children=(outer,))
    ts, cs = await _stores(trees=[root], chunks=[_chunk(f"{_MOD}.O.I"), _chunk(f"{_MOD}.O")])
    out = await _step(ts, cs).run(
        _state(
            [
                _chunk(f"{_MOD}.O.I", 0.9),
                _chunk(f"{_MOD}.O.I.leaf0", 0.8),
                _chunk(f"{_MOD}.O.I.leaf1", 0.7),
                _chunk(f"{_MOD}.O.s0", 0.6),
            ]
        )
    )
    # inner collapses (self-folded), s0 kept, NO outer rollup.
    assert _qnames(out) == [f"{_MOD}.O.I", f"{_MOD}.O.s0"]


@pytest.mark.asyncio
async def test_ac33b_different_content_same_qname_not_deduped() -> None:
    # Two chunks share qualified_name pkg.mod.C but have DIFFERENT content
    # (hence different content_hash). The dedup key is (qname, content_hash),
    # so BOTH must survive. A qname-only dedup key would wrongly drop one.
    other_content = Chunk(
        text="DIFFERENT-TEXT-for-C",
        relevance=0.95,
        metadata={"package": _PKG, "module": "pkg.dual", "qualified_name": f"{_MOD}.C"},
    )
    parent_row = _chunk(f"{_MOD}.C")  # text "text-pkg.mod.C" -> a different hash
    assert parent_row.content_hash != other_content.content_hash  # guard the premise
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[parent_row])
    out = await _step(ts, cs).run(
        _state(
            [
                other_content,
                _chunk(f"{_MOD}.C.m0", 0.9),
                _chunk(f"{_MOD}.C.m1", 0.8),
            ]
        )
    )
    texts = [c.text for c in out.candidates.items]
    assert "DIFFERENT-TEXT-for-C" in texts
    assert "text-pkg.mod.C" in texts
    assert len(texts) == 2


# ── AC34: blueprint loadability ──────────────────────────────────────────

_BLUEPRINT_YAML = """
name: chunk_search_rollup_tail
steps:
  - name: topk
    type: top_k_filter
    params:
      k: 50
  - name: rollup
    type: parent_rollup
    params:
      min_coverage: 0.4
      min_coverage_by_kind:
        class: 0.25
        module: 0.7
        markdown_heading: 0.45
  - name: limit
    type: limit
    params:
      max_results: 8
"""


def test_ac34_blueprint_params_reach_the_built_step() -> None:
    from pydocs_mcp.retrieval.pipeline.code_pipeline import CodeRetrieverPipeline

    pipeline = CodeRetrieverPipeline.from_dict(yaml.safe_load(_BLUEPRINT_YAML), _ctx())
    rollup = next(s for s in pipeline.stages if isinstance(s, ParentRollupStep))
    # Non-default values pin that nested `params:` (including the mapping)
    # reach the built step — the loader drops flat entry-level keys silently.
    assert rollup.min_coverage == 0.4
    assert dict(rollup.min_coverage_by_kind) == {
        "class": 0.25,
        "module": 0.7,
        "markdown_heading": 0.45,
    }
