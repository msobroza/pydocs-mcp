"""The branch pin inside the retrieval pipeline (spec §6.4, #312).

``SearchQuery.branch`` is the resolved branch a search answers from. The
pre-filter step ANDs the pin into the tree it hands every fetcher — after
validating the request's own filter, like #346's exclusion — so the lexical
fetch and the dense allowlist read the branch's membership; graph expansion
walks the branch's graph and hydrates the branch's rows. An empty branch
changes nothing: the tree, and so the dense ANN path, stays the request's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.filters import All, FieldEq
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchSlice,
    Chunk,
    ChunkFilterField,
    ChunkList,
    SearchQuery,
)
from pydocs_mcp.retrieval.filter_helpers import DEPENDENCY_DECISION_EXCLUSION
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.graph_expand import GraphExpandStep
from pydocs_mcp.retrieval.steps.pre_filter import PreFilterStep
from pydocs_mcp.storage.branch_records import ChunkMembership
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from pydocs_mcp.storage.node_reference import NodeReference

MAIN, FEATURE = "main", "feature/x"
_BRANCH_PIN = (
    FieldEq(ChunkFilterField.BRANCH.value, FEATURE),
    FieldEq(ChunkFilterField.SLICE.value, BranchSlice.TREE.value),
)


def _step(target_field: str) -> PreFilterStep:
    return PreFilterStep(
        allowed_fields=frozenset({"package", "scope"}),
        schema_name=target_field,
        target_field=target_field,  # type: ignore[arg-type]
    )


def _query(branch: str = "", *, exclude: bool = False, **pre_filter: str) -> SearchQuery:
    return SearchQuery(
        terms="x",
        pre_filter={"scope": "all", **pre_filter},
        exclude_dependency_decisions=exclude,
        branch=branch,
    )


async def _tree(step: PreFilterStep, query: SearchQuery):
    out = await step.run(RetrieverState(query=query))
    return out.scratch["pre_filter.result"].tree


async def test_a_pinned_chunk_search_filters_on_the_branch_and_its_tree_slice() -> None:
    assert await _tree(_step("chunk"), _query(FEATURE)) == All(_BRANCH_PIN)


async def test_the_pin_joins_the_request_filter_after_validation() -> None:
    tree = await _tree(_step("chunk"), _query(FEATURE, package="demo"))
    assert tree == All((FieldEq("package", "demo"), *_BRANCH_PIN))


async def test_the_pin_composes_with_the_dependency_decision_exclusion() -> None:
    tree = await _tree(_step("chunk"), _query(FEATURE, exclude=True))
    assert tree == All((DEPENDENCY_DECISION_EXCLUSION, *_BRANCH_PIN))


async def test_a_member_search_pins_the_branch_only() -> None:
    """Members have no slices: the member side reads the branch plus the dependency tier."""
    tree = await _tree(_step("member"), _query(FEATURE, exclude=True))
    assert tree == All((FieldEq(ChunkFilterField.BRANCH.value, FEATURE),))


async def test_no_branch_leaves_the_tree_the_request_s() -> None:
    assert await _tree(_step("chunk"), _query()) is None
    assert await _tree(_step("member"), _query()) is None


def test_a_branch_pin_needs_a_pre_filter_to_ride_on() -> None:
    with pytest.raises(ValueError, match="branch='main' needs a pre_filter"):
        SearchQuery(terms="x", branch=MAIN)


# ── Graph expansion walks the pinned branch's graph and rows ──


def _code(qname: str, text: str) -> Chunk:
    return Chunk(
        text=text,
        metadata={
            ChunkFilterField.PACKAGE.value: PROJECT_PACKAGE_NAME,
            "qualified_name": qname,
        },
    )


def _calls(caller: str, callee: str) -> NodeReference:
    return NodeReference(
        PROJECT_PACKAGE_NAME, caller, callee.rsplit(".", 1)[-1], callee, ReferenceKind.CALLS
    )


async def _seed_graph(db: Path) -> dict[str, int]:
    """``encode`` is shared; ``save`` differs per branch (two rows, one qname);
    ``legacy`` is main's callee, ``checksum`` feature/x's."""
    open_index_database(db).close()
    rows = {
        "encode": _code("app.core.encode", "def encode(row): ..."),
        "save@main": _code("app.core.save", "def save(row): return encode(row)"),
        "save@feature": _code("app.core.save", "def save(row): return encode(row) or 0  # v2"),
        "legacy": _code("app.core.legacy", "def legacy(): ..."),
        "checksum": _code("app.core.checksum", "def checksum(): ..."),
    }
    async with build_sqlite_uow_factory(db)() as uow:
        inserted = await uow.chunks.insert_returning_ids(tuple(rows.values()))
        ids = dict(zip(rows, inserted, strict=True))
        on_main = ("encode", "save@main", "legacy")
        on_feature = ("encode", "save@feature", "checksum")
        for branch, names in ((MAIN, on_main), (FEATURE, on_feature)):
            members = [ChunkMembership(branch, ids[n], "app/core.py") for n in names]
            await uow.branch_chunks.replace_membership(branch, members)
        main_edges = [
            _calls("app.core.save", "app.core.encode"),
            _calls("app.core.encode", "app.core.legacy"),
        ]
        feature_edges = [
            _calls("app.core.save", "app.core.encode"),
            _calls("app.core.encode", "app.core.checksum"),
        ]
        await uow.references.save_many(main_edges, package=PROJECT_PACKAGE_NAME, branch=MAIN)
        await uow.references.save_many(feature_edges, package=PROJECT_PACKAGE_NAME, branch=FEATURE)
        await uow.commit()
    return ids


async def _expanded(db: Path, branch: str, seed_id: int) -> dict[str, int | None]:
    step = GraphExpandStep(uow_factory=build_sqlite_uow_factory(db))
    seed = Chunk(
        text="def encode(row): ...",
        id=seed_id,
        relevance=0.9,
        metadata={"qualified_name": "app.core.encode", "package": PROJECT_PACKAGE_NAME},
    )
    query = SearchQuery(terms="q", pre_filter={"scope": "all"}, branch=branch)
    state = RetrieverState(query=query, candidates=ChunkList(items=(seed,)))
    out = await step.run(state)
    assert isinstance(out.candidates, ChunkList)
    return {c.metadata["qualified_name"]: c.id for c in out.candidates.items}


async def test_graph_expansion_follows_the_pinned_branch(tmp_path: Path) -> None:
    db = tmp_path / "graph.db"
    ids = await _seed_graph(db)
    assert await _expanded(db, FEATURE, ids["encode"]) == {
        "app.core.encode": ids["encode"],
        "app.core.save": ids["save@feature"],
        "app.core.checksum": ids["checksum"],
    }
    assert await _expanded(db, MAIN, ids["encode"]) == {
        "app.core.encode": ids["encode"],
        "app.core.save": ids["save@main"],
        "app.core.legacy": ids["legacy"],
    }
