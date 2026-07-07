"""SqliteReferenceStore GOVERNS query tests (spec §D18).

``find_governing(qname)`` → decision keys whose GOVERNS edge RESOLVES to that
qname (``to_node_id == qname``, resolver-backed, exact — not a substring scan).
``find_governed_by(decision_key)`` → the reverse: the resolved qnames a decision
governs. Both key decisions by the ``decision:<key>`` ``from_node_id`` convention
the ``emit_governs_edges`` stage stamps.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.reference_service import ReferenceService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Package, PackageOrigin
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.sqlite import SqliteReferenceStore


def _governs(*, key: str, to_name: str, to_node_id: str | None) -> NodeReference:
    return NodeReference(
        from_package="__project__",
        from_node_id=f"decision:{key}",
        to_name=to_name,
        to_node_id=to_node_id,
        kind=ReferenceKind.GOVERNS,
    )


@pytest.fixture
def provider(tmp_path):
    db = tmp_path / "x.db"
    open_index_database(db).close()
    return PerCallConnectionProvider(cache_path=db)


@pytest.mark.asyncio
async def test_find_governing_returns_resolved_decision_keys(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            _governs(key="greeting-pure", to_name="app.greet", to_node_id="app.greet"),
            _governs(key="cache-choice", to_name="app.cache", to_node_id="app.cache"),
        ],
        package="__project__",
    )
    keys = await store.find_governing("app.greet")
    assert keys == ["greeting-pure"]


@pytest.mark.asyncio
async def test_find_governing_ignores_unresolved_edges(provider):
    # An unresolved GOVERNS edge (to_node_id NULL) names a qname outside the
    # indexed universe — it must not answer find_governing for that qname.
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [_governs(key="greeting-pure", to_name="app.greet", to_node_id=None)],
        package="__project__",
    )
    assert await store.find_governing("app.greet") == []


@pytest.mark.asyncio
async def test_find_governing_only_governs_kind(provider):
    # A CALLS edge to the same qname must not leak into the governance answer.
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            NodeReference(
                from_package="__project__",
                from_node_id="app.caller",
                to_name="app.greet",
                to_node_id="app.greet",
                kind=ReferenceKind.CALLS,
            ),
            _governs(key="greeting-pure", to_name="app.greet", to_node_id="app.greet"),
        ],
        package="__project__",
    )
    assert await store.find_governing("app.greet") == ["greeting-pure"]


@pytest.mark.asyncio
async def test_find_governed_by_returns_resolved_qnames(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            _governs(key="greeting-pure", to_name="app.greet", to_node_id="app.greet"),
            _governs(key="greeting-pure", to_name="app.hello", to_node_id="app.hello"),
            _governs(key="greeting-pure", to_name="app.missing", to_node_id=None),
        ],
        package="__project__",
    )
    governed = await store.find_governed_by("greeting-pure")
    assert set(governed) == {"app.greet", "app.hello"}
    assert "app.missing" not in governed  # unresolved excluded


@pytest.mark.asyncio
async def test_governed_qnames_is_resolved_anti_join_set(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            _governs(key="k1", to_name="app.greet", to_node_id="app.greet"),
            _governs(key="k2", to_name="app.hello", to_node_id="app.hello"),
            _governs(key="k3", to_name="app.missing", to_node_id=None),  # unresolved
        ],
        package="__project__",
    )
    assert await store.governed_qnames() == frozenset({"app.greet", "app.hello"})


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


@pytest.mark.asyncio
async def test_e2e_governs_edge_resolves_through_reindex(tmp_path):
    """A GOVERNS edge passed to ``reindex_package`` is resolved by the existing
    resolver (no new resolver code) and becomes findable via ``find_governing``.

    Mirrors the CALLS/IMPORTS resolution e2e: the edge starts unresolved
    (``to_node_id=None``); once ``app.greet`` is in the indexed qname universe,
    the resolver flips ``to_node_id=to_name`` and the governance query answers.
    """
    db = tmp_path / "x.db"
    open_index_database(db).close()
    uow_factory = build_sqlite_uow_factory(db)
    indexing = IndexingService(uow_factory=uow_factory)
    ref_svc = ReferenceService(uow_factory=uow_factory)

    pkg = Package(
        name="__project__",
        version="0.1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.PROJECT,
    )
    tree = _module_tree("app.greet")
    governs = NodeReference(
        from_package="__project__",
        from_node_id="decision:greeting-pure",
        to_name="app.greet",
        to_node_id=None,  # unresolved at emit time — resolver fills it
        kind=ReferenceKind.GOVERNS,
    )
    await indexing.reindex_package(
        pkg,
        chunks=(),
        module_members=(),
        trees=(tree,),
        references=(governs,),
    )

    # Resolver flipped to_node_id → the governance query answers by qname.
    async with uow_factory() as uow:
        assert await uow.references.find_governing("app.greet") == ["greeting-pure"]
        assert await uow.references.governed_qnames() == frozenset({"app.greet"})

    # ReferenceService.governed_by surfaces the resolved GOVERNS edge as a row.
    rows = await ref_svc.governed_by("__project__", "app.greet")
    assert len(rows) == 1
    assert rows[0].from_node_id == "decision:greeting-pure"
    assert rows[0].to_node_id == "app.greet"
    assert rows[0].kind is ReferenceKind.GOVERNS
