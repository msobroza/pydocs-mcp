"""AC #11 + #12 — UPSERT semantics + canonical_dotted stability."""

from __future__ import annotations

import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.reference_service import ReferenceService
from pydocs_mcp.db import open_index_database
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


@pytest.mark.asyncio
async def test_pk_collision_does_not_crash_on_reindex(tmp_path):
    """AC #11 — re-indexing the same source updates instead of crashing."""
    db = tmp_path / "x.db"
    open_index_database(db).close()
    uow_factory = build_sqlite_uow_factory(db)
    indexing = IndexingService(uow_factory=uow_factory)
    ref_svc = ReferenceService(uow_factory=uow_factory)

    ref = NodeReference(
        from_package="pkg",
        from_node_id="pkg.a",
        to_name="requests.get",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    await indexing.reindex_package(
        _pkg("pkg"),
        chunks=(),
        module_members=(),
        trees=(),
        references=(ref,),
    )
    # Re-index the same package — same PK row.
    await indexing.reindex_package(
        _pkg("pkg"),
        chunks=(),
        module_members=(),
        trees=(),
        references=(ref,),
    )
    rows = await ref_svc.find_by_name("requests.get")
    # Exactly one row — UPSERT overwrote, no PK violation, no duplication.
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_canonical_dotted_output_is_stable_across_invocations():
    """AC #12 — canonical_dotted output is byte-stable; no row churn on re-extraction.

    Sanity check: parsing and walking the same source twice produces the
    same to_name string. Pin the contract that the resolver's PK is
    stable across Python versions / re-runs.
    """
    import ast

    from pydocs_mcp.extraction.strategies.references import canonical_dotted

    src = "a.b.c.d.e()"
    expr = ast.parse(src, mode="exec").body[0].value
    out1 = canonical_dotted(expr.func)
    expr2 = ast.parse(src, mode="exec").body[0].value
    out2 = canonical_dotted(expr2.func)
    assert out1 == out2
    assert out1 == "a.b.c.d.e"
