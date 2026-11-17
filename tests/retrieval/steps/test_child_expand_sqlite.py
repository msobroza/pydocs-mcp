"""child_expand on real SQLite: a pinned search reads its branch's tree and rows (#393).

The in-memory fakes ignore filter trees, so this pins the step's
branch-pinned qualified-name read and its branch-aware tree load against the
real adapter — with the parent candidate read back from the chunks table, so
it carries exactly the metadata a fetched candidate carries (no ``kind``).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.model.document_node import DocumentNode
from pydocs_mcp.filters import FieldEq
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.child_expand import ChildExpandStep
from pydocs_mcp.storage.branch_records import ChunkMembership
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from tests.retrieval.steps._child_expand_builders import chunk_row, class_node, module_node

_MAIN, _FEATURE = "main", "feature/x"
_CORE = "app.core"


def _store_tree(*method_names: str) -> DocumentNode:
    return module_node(class_node(f"{_CORE}.Store", *method_names), qname=_CORE)


def _project_row(qname: str, text: str) -> Chunk:
    return chunk_row(qname, module=_CORE, package=PROJECT_PACKAGE_NAME, text=text)


async def _seed_branches(db: Path) -> dict[str, int]:
    """``Store.save`` differs per branch (two rows, one qname); main has
    ``Store.load``, feature/x replaced it with ``Store.checksum``."""
    open_index_database(db).close()
    rows = {
        "Store": _project_row(f"{_CORE}.Store", "class Store:"),
        "save@main": _project_row(f"{_CORE}.Store.save", "def save(self): ..."),
        "save@feature": _project_row(f"{_CORE}.Store.save", "def save(self): ...  # v2"),
        "load": _project_row(f"{_CORE}.Store.load", "def load(self): ..."),
        "checksum": _project_row(f"{_CORE}.Store.checksum", "def checksum(self): ..."),
    }
    held = {_MAIN: ("Store", "save@main", "load"), _FEATURE: ("Store", "save@feature", "checksum")}
    trees = {_MAIN: _store_tree("save", "load"), _FEATURE: _store_tree("save", "checksum")}
    async with build_sqlite_uow_factory(db)() as uow:
        inserted = await uow.chunks.insert_returning_ids(tuple(rows.values()))
        ids = dict(zip(rows, inserted, strict=True))
        for branch, names in held.items():
            members = [ChunkMembership(branch, ids[n], "app/core.py") for n in names]
            await uow.branch_chunks.replace_membership(branch, members)
            await uow.trees.save_many([trees[branch]], package=PROJECT_PACKAGE_NAME, branch=branch)
        await uow.commit()
    return ids


async def _candidate_read_back(db: Path, qname: str) -> Chunk:
    """The candidate exactly as the chunks table returns it (no ``kind`` key)."""
    async with build_sqlite_uow_factory(db)() as uow:
        (row,) = await uow.chunks.list(filter=FieldEq(field="qualified_name", value=qname))
    return replace(row, relevance=0.9)


async def _children_on(db: Path, branch: str) -> dict[str, int | None]:
    step = ChildExpandStep(uow_factory=build_sqlite_uow_factory(db))
    parent = await _candidate_read_back(db, f"{_CORE}.Store")
    query = SearchQuery(terms="q", pre_filter={"scope": "all"}, branch=branch, max_results=8)
    out = await step.run(RetrieverState(query=query, candidates=ChunkList(items=(parent,))))
    assert isinstance(out.candidates, ChunkList)
    return {c.metadata["qualified_name"]: c.id for c in out.candidates.items[1:]}


async def test_a_pinned_search_appends_the_children_its_branch_holds(tmp_path: Path) -> None:
    db = tmp_path / "children.db"
    ids = await _seed_branches(db)

    assert await _children_on(db, _FEATURE) == {
        f"{_CORE}.Store.save": ids["save@feature"],
        f"{_CORE}.Store.checksum": ids["checksum"],
    }
    assert await _children_on(db, _MAIN) == {
        f"{_CORE}.Store.save": ids["save@main"],
        f"{_CORE}.Store.load": ids["load"],
    }
