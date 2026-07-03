"""End-to-end: capture -> resolve -> store -> ReferenceService read.

Single test runs the full plumbing: ingestion pipeline (capture stage),
IndexingService.reindex_package (resolver + write), then queries via
ReferenceService over the real SqliteReferenceStore.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.reference_service import ReferenceService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from pydocs_mcp.storage.node_reference import NodeReference


def _pkg(name: str) -> Package:
    return Package(
        name=name,
        version="0.1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )


def _module_tree(qname: str) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname,
        kind=NodeKind.MODULE,
        source_path=f"{qname.replace('.', '/')}.py",
        start_line=1,
        end_line=10,
        text="",
        content_hash="h",
    )


def _ref(**kw) -> NodeReference:
    base = dict(
        from_package="pkg",
        from_node_id="pkg.utils.runner",
        to_name="do_it",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    base.update(kw)
    return NodeReference(**base)


@pytest.mark.asyncio
async def test_e2e_index_resolve_store_query(tmp_path):
    db = tmp_path / "x.db"
    open_index_database(db).close()
    uow_factory = build_sqlite_uow_factory(db)
    indexing = IndexingService(uow_factory=uow_factory)
    ref_svc = ReferenceService(uow_factory=uow_factory)

    # First index pkg.helpers so its qname is in the universe.
    helpers_pkg = _pkg("pkg")  # use single "pkg" since trees carry the dotted module qname
    helpers_tree = _module_tree("pkg.helpers.compute")
    await indexing.reindex_package(
        helpers_pkg,
        chunks=(),
        module_members=(),
        trees=(helpers_tree,),
    )

    # Now index pkg.utils — alias `do_it` -> `pkg.helpers.compute`.
    utils_tree = _module_tree("pkg.utils.runner")
    raw_refs = (_ref(),)
    aliases = {"pkg.utils": {"do_it": "pkg.helpers.compute"}}
    await indexing.reindex_package(
        helpers_pkg,
        chunks=(),
        module_members=(),
        trees=(helpers_tree, utils_tree),  # both trees in universe
        references=raw_refs,
        reference_aliases=aliases,
    )

    # Query — both find_callers AND find_by_name should see the resolved row.
    # Decision C1: callers() takes (package, qname) — `pkg` is informational,
    # the storage is cross-package.
    callers = await ref_svc.callers("pkg", "pkg.helpers.compute")
    assert len(callers) == 1
    assert callers[0].from_node_id == "pkg.utils.runner"
    assert callers[0].to_node_id == "pkg.helpers.compute"

    # `find_by_name` matches the captured raw `to_name`, NOT the resolved
    # `to_node_id` — the resolver fills `to_node_id` but leaves `to_name`
    # verbatim (see reference_service.find_by_name docstring: "queryable
    # for both resolved AND unresolved edges"). So we query by "do_it".
    by_name = await ref_svc.find_by_name(
        "do_it",
        kind=ReferenceKind.CALLS,
    )
    assert len(by_name) == 1
    assert by_name[0].to_node_id == "pkg.helpers.compute"


@pytest.mark.asyncio
async def test_e2e_impact_ranks_transitive_callers(tmp_path):
    """Full pipeline: resolve + store, then ReferenceService.impact runs the
    real recursive-CTE over SQLite and ranks the blast-radius.

    Graph: pkg.indirect -> pkg.direct -> pkg.target. Blast-radius of
    pkg.target is pkg.direct (hop 1), pkg.indirect (hop 2). node_scores is
    disabled by default → fan-in fallback ranking (no PageRank).
    """
    db = tmp_path / "x.db"
    open_index_database(db).close()
    uow_factory = build_sqlite_uow_factory(db)
    indexing = IndexingService(uow_factory=uow_factory)
    ref_svc = ReferenceService(uow_factory=uow_factory)

    pkg = _pkg("pkg")
    trees = (
        _module_tree("pkg.target"),
        _module_tree("pkg.direct"),
        _module_tree("pkg.indirect"),
    )
    # Raw (unresolved) edges — the resolver fills to_node_id by exact qname match.
    refs = (
        _ref(from_node_id="pkg.direct", to_name="pkg.target", to_node_id=None),
        _ref(from_node_id="pkg.indirect", to_name="pkg.direct", to_node_id=None),
    )
    await indexing.reindex_package(pkg, chunks=(), module_members=(), trees=trees, references=refs)

    out = await ref_svc.impact("pkg", "pkg.target", max_depth=2, limit=10)
    assert [(n.qualified_name, n.hop) for n in out] == [
        ("pkg.direct", 1),
        ("pkg.indirect", 2),
    ]
    assert all(not n.has_scores for n in out)  # node_scores disabled by default


def _src_chunk(qname: str, *, text: str) -> Chunk:
    return Chunk(text=text, metadata={"package": "pkg", "qualified_name": qname})


@pytest.mark.asyncio
async def test_e2e_context_packs_dependency_closure(tmp_path):
    """Full pipeline: resolve + store chunks, then ReferenceService.context
    forward-walks the closure and hydrates focus/ring source from real chunks."""
    db = tmp_path / "x.db"
    open_index_database(db).close()
    uow_factory = build_sqlite_uow_factory(db)
    indexing = IndexingService(uow_factory=uow_factory)
    ref_svc = ReferenceService(uow_factory=uow_factory)

    pkg = _pkg("pkg")
    trees = (_module_tree("pkg.seed"), _module_tree("pkg.dep"))
    refs = (_ref(from_node_id="pkg.seed", to_name="pkg.dep", to_node_id=None),)  # seed calls dep
    chunks = (
        _src_chunk("pkg.seed", text="def seed():\n    dep()"),
        _src_chunk("pkg.dep", text="def dep():\n    pass"),
    )
    await indexing.reindex_package(
        pkg, chunks=chunks, module_members=(), trees=trees, references=refs
    )

    out = await ref_svc.context("pkg", "pkg.seed", max_depth=2, limit=10)
    assert [(n.qualified_name, n.hop) for n in out] == [("pkg.seed", 0), ("pkg.dep", 1)]
    assert (
        out[0].source_text == "def seed():\n    dep()"
    )  # focus = full source (survives round-trip)
    assert out[1].source_text == "def dep():\n    pass"  # ring renderer derives signature from this
