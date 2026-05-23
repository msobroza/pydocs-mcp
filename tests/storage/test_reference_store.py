"""End-to-end SqliteReferenceStore tests (spec §6.2)."""
from __future__ import annotations

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.retrieval.pipeline_legacy import PerCallConnectionProvider
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.sqlite import SqliteReferenceStore, SqliteUnitOfWork


def _ref(**kw) -> NodeReference:
    base = dict(
        from_package="pkg",
        from_node_id="pkg.mod.fn",
        to_name="other.symbol",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    base.update(kw)
    return NodeReference(**base)


@pytest.fixture
def provider(tmp_path):
    db = tmp_path / "x.db"
    open_index_database(db).close()
    return PerCallConnectionProvider(cache_path=db)


@pytest.mark.asyncio
async def test_save_many_then_find_callers(provider):
    store = SqliteReferenceStore(provider=provider)
    refs = [
        _ref(from_node_id="pkg.a", to_name="t", to_node_id="t",
             kind=ReferenceKind.CALLS),
        _ref(from_package="other", from_node_id="other.x", to_name="t",
             to_node_id="t", kind=ReferenceKind.CALLS),
    ]
    await store.save_many(refs, package="pkg")
    callers = await store.find_callers(target_node_id="t")
    assert {r.from_package for r in callers} == {"pkg", "other"}


@pytest.mark.asyncio
async def test_save_many_then_find_callees(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            _ref(from_node_id="pkg.a", to_name="x", to_node_id="x"),
            _ref(from_node_id="pkg.b", to_name="y", to_node_id="y"),
        ],
        package="pkg",
    )
    callees = await store.find_callees(from_node_id="pkg.a")
    assert {r.to_name for r in callees} == {"x"}


@pytest.mark.asyncio
async def test_find_by_name_filter_by_kind(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            _ref(to_name="os.path.join", kind=ReferenceKind.CALLS),
            _ref(to_name="os.path.join", kind=ReferenceKind.IMPORTS,
                 from_node_id="pkg.b"),
        ],
        package="pkg",
    )
    all_hits = await store.find_by_name("os.path.join")
    assert len(all_hits) == 2
    calls_only = await store.find_by_name(
        "os.path.join", ReferenceKind.CALLS,
    )
    assert {r.kind for r in calls_only} == {ReferenceKind.CALLS}


@pytest.mark.asyncio
async def test_save_many_upsert_on_pk_collision(provider):
    """spec Decision §6.2 — INSERT ON CONFLICT DO UPDATE SET to_node_id.

    Calling save_many twice with the same (from_package, from_node_id,
    to_name, kind) but DIFFERENT to_node_id must update, not crash.
    """
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [_ref(to_name="x", to_node_id=None)], package="pkg",
    )
    await store.save_many(
        [_ref(to_name="x", to_node_id="pkg.real.x")], package="pkg",
    )
    rows = await store.find_by_name("x")
    assert len(rows) == 1
    assert rows[0].to_node_id == "pkg.real.x"


@pytest.mark.asyncio
async def test_delete_for_package_scoped(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            _ref(from_package="pkg", to_name="x"),
            _ref(from_package="other", from_node_id="other.x", to_name="y"),
        ],
        package="pkg",
    )
    await store.delete_for_package("pkg")
    rows_x = await store.find_by_name("x")
    rows_y = await store.find_by_name("y")
    assert rows_x == []
    assert len(rows_y) == 1


@pytest.mark.asyncio
async def test_delete_all_wipes_everything(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([_ref()], package="pkg")
    await store.delete_all()
    rows = await store.find_by_name("other.symbol")
    assert rows == []


@pytest.mark.asyncio
async def test_save_many_zero_refs_is_noop(provider):
    """No-op fast path avoids a useless executemany call."""
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([], package="pkg")
    rows = await store.find_by_name("anything")
    assert rows == []


@pytest.mark.asyncio
async def test_save_many_inside_uow_shares_connection(tmp_path):
    """spec §14.4 — writes inside a SqliteUnitOfWork share the held conn."""
    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    async with SqliteUnitOfWork(provider=provider) as uow:
        await uow.references.save_many([_ref()], package="pkg")
        await uow.commit()
    # Reopen — row survived.
    store = SqliteReferenceStore(provider=provider)
    rows = await store.find_by_name("other.symbol")
    assert len(rows) == 1
