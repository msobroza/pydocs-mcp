"""IndexingService merges stdlib qnames into resolver universe (AC #15 follow-up)."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_reindex_resolves_stdlib_call_target(tmp_path, monkeypatch):
    """End-to-end: a project that calls `os.path.join` produces a resolved
    NodeReference with to_node_id='os.path.join' (was None pre-#stdlib-idx)."""
    from pydocs_mcp.application.indexing_service import IndexingService
    from pydocs_mcp.application.reference_service import ReferenceService
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.extraction.strategies import stdlib_qnames as stdlib_mod
    from pydocs_mcp.retrieval.config import ReferenceResolverConfig
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory
    from pydocs_mcp.storage.node_reference import NodeReference

    db = tmp_path / "x.db"
    open_index_database(db).close()
    uow_factory = build_sqlite_uow_factory(db)
    indexing = IndexingService(uow_factory=uow_factory)
    ref_svc = ReferenceService(uow_factory=uow_factory)

    # Seed an unresolved CALL referencing os.path.join.
    from pydocs_mcp.models import Package, PackageOrigin

    pkg = Package(
        name="myproj",
        version="0.1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.PROJECT,
    )
    raw = (
        NodeReference(
            from_package="myproj",
            from_node_id="myproj.utils.runner",
            to_name="os.path.join",
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
    )

    # With include_stdlib=True (default), os.path.join resolves.
    orig = stdlib_mod._get_resolver_config()
    stdlib_mod._set_resolver_config(ReferenceResolverConfig(include_stdlib=True))
    try:
        await indexing.reindex_package(
            pkg,
            chunks=(),
            module_members=(),
            trees=(),
            references=raw,
        )
        callers = await ref_svc.callers("os", "os.path.join")
        assert len(callers) == 1
        assert callers[0].to_node_id == "os.path.join"
        assert callers[0].from_node_id == "myproj.utils.runner"
    finally:
        stdlib_mod._set_resolver_config(orig)


@pytest.mark.asyncio
async def test_reindex_skips_stdlib_when_include_stdlib_false(tmp_path):
    """When include_stdlib=false, os.path.join stays unresolved (to_node_id=None)."""
    from pydocs_mcp.application.indexing_service import IndexingService
    from pydocs_mcp.application.reference_service import ReferenceService
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.extraction.strategies import stdlib_qnames as stdlib_mod
    from pydocs_mcp.retrieval.config import ReferenceResolverConfig
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory
    from pydocs_mcp.storage.node_reference import NodeReference

    db = tmp_path / "x.db"
    open_index_database(db).close()
    uow_factory = build_sqlite_uow_factory(db)
    indexing = IndexingService(uow_factory=uow_factory)
    ref_svc = ReferenceService(uow_factory=uow_factory)

    from pydocs_mcp.models import Package, PackageOrigin

    pkg = Package(
        name="myproj",
        version="0.1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.PROJECT,
    )
    raw = (
        NodeReference(
            from_package="myproj",
            from_node_id="myproj.utils.runner",
            to_name="os.path.join",
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
    )

    orig = stdlib_mod._get_resolver_config()
    stdlib_mod._set_resolver_config(ReferenceResolverConfig(include_stdlib=False))
    try:
        await indexing.reindex_package(
            pkg,
            chunks=(),
            module_members=(),
            trees=(),
            references=raw,
        )
        # Cross-package callers query — should return nothing because the
        # row is still unresolved (to_node_id=None).
        callers = await ref_svc.callers("os", "os.path.join")
        assert callers == ()
        # But the row exists under from_node_id; find_by_name finds it.
        all_calls = await ref_svc.find_by_name("os.path.join")
        assert len(all_calls) == 1
        assert all_calls[0].to_node_id is None
    finally:
        stdlib_mod._set_resolver_config(orig)
