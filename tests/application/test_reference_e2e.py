"""End-to-end: capture -> resolve -> store -> ReferenceService read.

Single test runs the full plumbing: ingestion pipeline (capture stage),
IndexingService.reindex_package (resolver + write), then queries via
ReferenceService over the real SqliteReferenceStore.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.application.indexing_service import IndexingService, ResolverInputs
from pydocs_mcp.application.reference_service import ReferenceService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Package, PackageOrigin
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
        resolver_inputs=ResolverInputs(aliases=aliases),
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
