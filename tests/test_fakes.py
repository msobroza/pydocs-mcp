"""Pin the FakeUnitOfWork + InMemory* contract."""

from __future__ import annotations

import pytest

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Chunk, ModuleMember, Package, PackageOrigin
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.protocols import UnitOfWork
from tests._fakes import (
    FakeUnitOfWork,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    InMemoryModuleMemberStore,
    InMemoryPackageStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)


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


def test_fake_unit_of_work_satisfies_protocol():
    """§14.9 AC #2 — FakeUnitOfWork passes isinstance(_, UnitOfWork)."""
    assert isinstance(FakeUnitOfWork(), UnitOfWork)


@pytest.mark.asyncio
async def test_fake_uow_committed_only_on_explicit_commit():
    """§14.9 AC #6 — committed flips True only after await commit()."""
    uow = FakeUnitOfWork()
    async with uow:
        assert uow.committed is False
        await uow.commit()
    assert uow.committed is True
    assert uow.rolled_back is False


@pytest.mark.asyncio
async def test_fake_uow_rolls_back_when_commit_not_called():
    """§14.9 AC #6 — exit without commit triggers rollback flag."""
    uow = FakeUnitOfWork()
    async with uow:
        pass
    assert uow.committed is False
    assert uow.rolled_back is True


@pytest.mark.asyncio
async def test_fake_uow_rolls_back_on_exception():
    """§14.9 AC #6 — exception in body triggers rollback."""
    uow = FakeUnitOfWork()
    with pytest.raises(ValueError):
        async with uow:
            raise ValueError("boom")
    assert uow.rolled_back is True


def test_fake_uow_attribute_outside_context_raises():
    """§14.9 AC #7 — outside-context repo access raises.

    SqliteUnitOfWork uses ``@property`` so bare attribute access raises
    directly. FakeUnitOfWork can't (``getattr_static`` bypasses
    properties on Python 3.12+, breaking ``isinstance(_, UnitOfWork)``),
    so the fake's repo attrs return a ``_NotEnteredProxy`` that raises
    on any method call — equivalent contract at the point of use.
    """
    uow = FakeUnitOfWork()
    with pytest.raises(UnitOfWorkNotEnteredError):
        # _NotEnteredProxy raises on any attribute / method access.
        # This mirrors what real services / tests would hit when they
        # try to actually use the repo without entering the context.
        uow.packages.get("anything")


@pytest.mark.asyncio
async def test_inmemory_package_store_list_matches_protocol_signature():
    """§14.9 AC #5 — list(filter, limit) signature matches real PackageStore.
    Catches the planned .all() mismatch eng plan-review flagged."""
    store = InMemoryPackageStore()
    result = await store.list(filter=None, limit=200)
    assert result == []


@pytest.mark.asyncio
async def test_make_fake_uow_factory_returns_callable():
    """§9 — helper returns a Callable[[], FakeUnitOfWork]."""
    factory = make_fake_uow_factory()
    assert callable(factory)
    uow = factory()
    assert isinstance(uow, FakeUnitOfWork)


@pytest.mark.asyncio
async def test_make_fake_uow_factory_returned_uows_share_underlying_stores():
    """§9 — each factory call returns a fresh UoW; underlying stores ARE shared."""
    packages = InMemoryPackageStore()
    factory = make_fake_uow_factory(packages=packages)

    uow1 = factory()
    uow2 = factory()
    assert uow1 is not uow2
    assert uow1.packages_store is packages
    assert uow2.packages_store is packages


@pytest.mark.asyncio
async def test_make_fake_uow_factory_is_re_entrance_safe():
    """§6 — each factory call returns an unentered UoW (re-entrance guard cleared)."""
    factory = make_fake_uow_factory()
    async with factory() as uow1:
        pass  # exit normally
    # Second call must succeed despite first having entered+exited.
    async with factory() as uow2:
        assert uow2._entered is True


@pytest.mark.asyncio
async def test_in_memory_package_store_records_calls():
    """§9.1 — InMemoryPackageStore.calls mirrors InMemoryDocumentTreeStore."""
    store = InMemoryPackageStore()
    pkg = Package(
        name="x",
        version="0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="",
        origin=PackageOrigin.DEPENDENCY,
    )
    await store.upsert(pkg)
    await store.get("x")
    assert any(c.method == "upsert" for c in store.calls)
    assert any(c.method == "get" for c in store.calls)


@pytest.mark.asyncio
async def test_in_memory_chunk_store_records_calls():
    """§9.1."""
    store = InMemoryChunkStore()
    chunk = Chunk(text="t", metadata={"package": "x"})
    await store.upsert([chunk])
    assert any(c.method == "upsert" for c in store.calls)


@pytest.mark.asyncio
async def test_in_memory_module_member_store_records_calls():
    """§9.1."""
    store = InMemoryModuleMemberStore()
    m = ModuleMember(metadata={"package": "x", "module": "x.m", "name": "f", "kind": "function"})
    await store.upsert_many([m])
    assert any(c.method == "upsert_many" for c in store.calls)


def test_not_entered_proxy_bool_raises_to_match_sqlite():
    """SqliteUnitOfWork raises UnitOfWorkNotEnteredError when its
    `@property` is accessed outside the context. The fake's proxy used to
    return truthy from __bool__, diverging in code like
    `if uow.packages: ...`. Make the fake match: any boolean coercion
    raises too."""
    from tests._fakes import _NotEnteredProxy

    proxy = _NotEnteredProxy("packages")
    with pytest.raises(UnitOfWorkNotEnteredError):
        bool(proxy)


@pytest.mark.asyncio
async def test_in_memory_reference_store_save_many_records_calls():
    """spec §6.2 — save_many appends to .calls and stores under by_package."""
    store = InMemoryReferenceStore()
    await store.save_many([_ref()], package="pkg")
    assert any(c.method == "save_many" for c in store.calls)
    assert "pkg" in store.by_package


@pytest.mark.asyncio
async def test_in_memory_reference_store_find_callers_cross_package():
    """Spec §6.2 — find_callers is cross-package (no package filter)."""
    store = InMemoryReferenceStore()
    await store.save_many(
        [
            _ref(
                from_package="pkg1",
                from_node_id="pkg1.a",
                to_node_id="t",
                to_name="t",
                kind=ReferenceKind.CALLS,
            ),
            _ref(
                from_package="pkg2",
                from_node_id="pkg2.b",
                to_node_id="t",
                to_name="t",
                kind=ReferenceKind.CALLS,
            ),
        ],
        package="pkg1",  # save_many call only carries one package label, but
        # by_package stores by from_package of each ref
    )
    callers = await store.find_callers(target_node_id="t")
    assert {r.from_package for r in callers} == {"pkg1", "pkg2"}


@pytest.mark.asyncio
async def test_in_memory_reference_store_find_callees_filters_by_source():
    store = InMemoryReferenceStore()
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
async def test_in_memory_reference_store_find_by_name_optional_kind_filter():
    store = InMemoryReferenceStore()
    await store.save_many(
        [
            _ref(to_name="os.path.join", kind=ReferenceKind.CALLS),
            _ref(to_name="os.path.join", kind=ReferenceKind.IMPORTS),
        ],
        package="pkg",
    )
    all_hits = await store.find_by_name("os.path.join")
    assert len(all_hits) == 2
    calls_only = await store.find_by_name("os.path.join", ReferenceKind.CALLS)
    assert {r.kind for r in calls_only} == {ReferenceKind.CALLS}


@pytest.mark.asyncio
async def test_in_memory_reference_store_delete_for_package():
    store = InMemoryReferenceStore()
    await store.save_many(
        [
            _ref(from_package="pkg1", to_name="x"),
            _ref(from_package="pkg2", to_name="y"),
        ],
        package="pkg1",
    )
    await store.delete_for_package("pkg1")
    rows = await store.find_by_name("x")
    assert rows == []
    rows = await store.find_by_name("y")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_in_memory_reference_store_delete_all():
    store = InMemoryReferenceStore()
    await store.save_many([_ref()], package="pkg")
    await store.delete_all()
    assert store.by_package == {}


@pytest.mark.asyncio
async def test_fake_uow_now_carries_references_store():
    """spec §14.7 — FakeUnitOfWork gains a 5th repo attribute."""
    factory = make_fake_uow_factory()
    async with factory() as uow:
        assert isinstance(uow.references, InMemoryReferenceStore)


@pytest.mark.asyncio
async def test_fake_uow_references_raises_outside_context():
    """spec §14.2 — outside `async with`, the references attribute raises."""
    uow = FakeUnitOfWork()
    # _NotEnteredProxy raises on any access, including bool(), incl. method calls.
    with pytest.raises(UnitOfWorkNotEnteredError):
        await uow.references.save_many([], package="pkg")


@pytest.mark.asyncio
async def test_make_fake_uow_factory_accepts_references_kwarg():
    """spec §14.7 — factory threads a shared InMemoryReferenceStore."""
    refs = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=refs)
    uow1 = factory()
    uow2 = factory()
    assert uow1.references_store is refs
    assert uow2.references_store is refs


@pytest.mark.asyncio
async def test_fake_uow_structurally_satisfies_widened_unit_of_work_protocol():
    """isinstance(FakeUnitOfWork(), UnitOfWork) holds for the post-#5b
    Protocol shape (5 attributes, not 4). Catches forgotten swap-in/out
    of the new ``references`` attribute on a future re-shape."""
    from pydocs_mcp.storage.protocols import UnitOfWork

    uow = FakeUnitOfWork()
    assert isinstance(uow, UnitOfWork)


@pytest.mark.asyncio
async def test_in_memory_chunk_store_list_filters_module_and_qualified_name():
    store = InMemoryChunkStore()
    await store.upsert(
        [
            Chunk(text="a", metadata={"package": "p", "module": "p.m", "qualified_name": "p.m.A"}),
            Chunk(text="b", metadata={"package": "p", "module": "p.m", "qualified_name": "p.m.B"}),
            Chunk(text="c", metadata={"package": "p", "module": "p.n", "qualified_name": "p.m.A"}),
        ]
    )
    # AND semantics across the CHUNK_COLUMNS-whitelisted keys the real
    # translator supports (storage/sqlite/filter_adapter.py CHUNK_COLUMNS).
    # No limit: a broken filter would return >1 row (c shares qualified_name
    # p.m.A but lives in module p.n), so this discriminates real AND-matching.
    rows = await store.list(filter={"package": "p", "module": "p.m", "qualified_name": "p.m.A"})
    assert [c.text for c in rows] == ["a"]
    # Module-only filter selects both p.m chunks (not the p.n one).
    assert {c.text for c in await store.list(filter={"package": "p", "module": "p.m"})} == {
        "a",
        "b",
    }
    # package-only behavior unchanged.
    assert len(await store.list(filter={"package": "p"})) == 3


@pytest.mark.asyncio
async def test_in_memory_document_tree_store_load_serves_by_module_and_records_call():
    from pydocs_mcp.extraction.model.document_node import DocumentNode, NodeKind

    store = InMemoryDocumentTreeStore()
    tree = DocumentNode(
        node_id="p.m",
        qualified_name="p.m",
        title="m",
        kind=NodeKind.MODULE,
        source_path="m.py",
        start_line=1,
        end_line=2,
        text="doc",
        content_hash="h",
    )
    await store.save_many([tree], package="p")
    # Mirrors SqliteDocumentTreeStore.load: the module argument equals the
    # tree root's qualified_name (the document_trees row key).
    assert await store.load("p", "p.m") is tree
    assert await store.load("p", "p.other") is None
    assert await store.load("other", "p.m") is None
    assert any(c.method == "load" and c.payload == ("p", "p.m") for c in store.calls)


def test_chunk_metadata_filter_keys_track_real_whitelist():
    # Guard: if CHUNK_COLUMNS gains a filterable metadata column, the fake
    # must gain it too, or it silently stops matching on it.
    from pydocs_mcp.storage.sqlite.filter_adapter import CHUNK_COLUMNS
    from tests._fakes import _CHUNK_METADATA_FILTER_KEYS

    assert set(_CHUNK_METADATA_FILTER_KEYS) == set(CHUNK_COLUMNS) - {"id", "package"}


@pytest.mark.asyncio
async def test_in_memory_chunk_store_refresh_span_metadata_matches_sqlite_semantics():
    """v15 span backfill parity: refresh_span_metadata updates ONLY the span
    metadata on (package, module, content_hash)-matched rows — id and the
    embedded flag survive, mirroring SqliteChunkRepository's UPDATE."""
    store = InMemoryChunkStore()
    stale = Chunk(
        text="def f(): ...",
        metadata={"package": "p", "module": "p.m", "title": "f"},
    )
    await store.insert((stale,))
    ((stored_id, _),) = await store.list_id_hash_pairs(filter={"package": "p"})
    await store.mark_embedded([stored_id])

    fresh = Chunk(
        text="def f(): ...",
        metadata={
            "package": "p",
            "module": "p.m",
            "title": "f",
            "source_path": "p/m.py",
            "start_line": 3,
            "end_line": 4,
        },
    )
    assert fresh.content_hash == stale.content_hash  # precondition: hash-matched
    await store.refresh_span_metadata("p", (fresh,))

    (row,) = await store.list(filter={"package": "p"})
    assert row.id == stored_id
    assert row.metadata.get("source_path") == "p/m.py"
    assert row.metadata.get("start_line") == 3
    assert row.metadata.get("end_line") == 4
    assert store.embedded_ids == {stored_id}
    assert any(c.method == "refresh_span_metadata" for c in store.calls)
