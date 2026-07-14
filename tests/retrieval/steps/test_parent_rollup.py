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
