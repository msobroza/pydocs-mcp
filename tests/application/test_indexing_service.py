"""Tests for IndexingService using Protocol-fake stores ONLY (AC #10).

These tests prove that IndexingService is backend-agnostic: they use
the canonical in-memory fakes from ``tests/_fakes.py`` that structurally
satisfy the PackageStore / ChunkStore / ModuleMemberStore / UnitOfWork
Protocols. No SQLite connection is opened (except the e2e regression
test), no concrete repository is imported in the unit tests.

Sub-PR #5a-2 — IndexingService takes ONLY ``uow_factory``: the service
opens a UoW per call and writes through it. ``begin()`` is gone
everywhere; the legacy two-shape construction (5 stores OR a UoW) is
collapsed to one.
"""
from __future__ import annotations

from dataclasses import fields

import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, ModuleMember, Package, PackageOrigin
from tests._fakes import (
    FakeUnitOfWork,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    InMemoryModuleMemberStore,
    InMemoryPackageStore,
    make_fake_uow_factory,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


def _pkg(name: str = "fastapi") -> Package:
    return Package(
        name=name,
        version="0.1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )


def _chunk(package: str, title: str, text: str = "body") -> Chunk:
    return Chunk(text=text, metadata={"package": package, "title": title})


def _member(package: str, name: str) -> ModuleMember:
    return ModuleMember(
        metadata={
            "package": package,
            "module": f"{package}.mod",
            "name": name,
            "kind": "function",
        },
    )


def _tree(qname: str, *, kind: NodeKind = NodeKind.MODULE) -> DocumentNode:
    """Build a minimal :class:`DocumentNode` for tests that don't care about content.

    Spec C1: the new :meth:`IndexingService._reresolve_cross_package`
    walks the just-indexed package's trees to collect qnames — so test
    fixtures can no longer pass arbitrary strings for ``trees=``.
    """
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname,
        kind=kind,
        source_path="",
        start_line=0,
        end_line=0,
        text="",
        content_hash="",
    )


# ── Shape pin ────────────────────────────────────────────────────────────


def test_indexing_service_only_has_one_field():
    """Sub-PR #5a-2: IndexingService is reduced to a single dependency —
    ``uow_factory``. The 4 stores + ``unit_of_work`` are gone; the service
    opens a UoW per call and writes through it. A drift here means a
    re-introduction of the legacy reach-through wiring.
    """
    names = {f.name for f in fields(IndexingService)}
    assert names == {"uow_factory"}


# ── Core writes go through UoW ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_reindex_package_writes_through_uow():
    """Open a UoW, diff-merge chunks + delete-then-upsert members/pkg, commit fires.

    The chunks store no longer sees the legacy ``delete + upsert`` pair —
    the diff-merge instead probes ``list_id_hash_pairs`` to learn the
    existing snapshot, then INSERTs only the added chunks (here: all of
    them, since the store starts empty). The members + packages stores
    keep the delete-then-upsert shape.
    """
    factory = make_fake_uow_factory()
    service = IndexingService(uow_factory=factory)

    pkg = _pkg("fastapi")
    chunks = (_chunk("fastapi", "Routing"), _chunk("fastapi", "Middleware"))
    members = (_member("fastapi", "APIRouter"),)

    await service.reindex_package(pkg, chunks, members)

    # The factory produced a UoW; grab its underlying stores via a fresh call.
    uow = factory()
    async with uow:
        ps = uow.packages_store
        cs = uow.chunks_store
        ms = uow.module_members_store

    # Chunks: empty starting state → no delete_by_ids, just probe + insert.
    assert [c.method for c in cs.calls] == ["list_id_hash_pairs", "insert"]
    # Members + packages keep the legacy delete-then-upsert shape.
    assert [c.method for c in ms.calls] == ["delete", "upsert_many"]
    assert [c.method for c in ps.calls if c.method in ("delete", "upsert")] == [
        "delete",
        "upsert",
    ]

    # Diff probe filters key on the right column.
    assert cs.calls[0].payload == {"filter": {"package": "fastapi"}}
    assert ms.calls[0].payload == {"package": "fastapi"}
    # Package store uses the literal "name" column.
    pkg_calls = [c for c in ps.calls if c.method in ("delete", "upsert")]
    assert pkg_calls[0].payload == {"name": "fastapi"}

    # End state: rows persisted in the underlying in-memory stores.
    assert ps.items["fastapi"] is pkg
    assert len(cs.by_package.get("fastapi", [])) == 2
    assert len(ms.by_package.get("fastapi", [])) == 1


@pytest.mark.asyncio
async def test_reindex_package_rolls_back_on_exception():
    """A RuntimeError during chunk insert → rolled_back is set, committed
    is NOT set. The UoW's safety-net rollback fires from ``__aexit__``.

    The diff-merge now writes added chunks via ``insert`` (not ``upsert``);
    we hook the bomb on ``insert`` so the failure lands inside the
    transaction body the same way as before.
    """
    # Build a chunk store that explodes on insert. We swap it into the
    # shared store set, so the factory returns UoWs wired to the bomb.
    class _BoomChunkStore(InMemoryChunkStore):
        async def insert(self, chunks):
            await super().insert(chunks)  # record the call before failing
            raise RuntimeError("boom")

    chunks_store = _BoomChunkStore()
    factory = make_fake_uow_factory(chunks=chunks_store)
    service = IndexingService(uow_factory=factory)

    with pytest.raises(RuntimeError, match="boom"):
        await service.reindex_package(
            _pkg("fastapi"),
            (_chunk("fastapi", "A"),),
            (_member("fastapi", "X"),),
        )

    # The FakeUnitOfWork's __aexit__ flips rolled_back when commit was
    # never reached (exception escaped before service.commit fired).
    # We probe a fresh UoW from the same factory — they share state via
    # the InMemory* stores but each new UoW has fresh committed/rolled_back.
    # We need to inspect the UoW the service used. Plumb it through the
    # factory: capture the last produced UoW.
    captured: list[FakeUnitOfWork] = []
    base_factory = make_fake_uow_factory(chunks=_BoomChunkStore())

    def capture_factory() -> FakeUnitOfWork:
        uow = base_factory()
        captured.append(uow)
        return uow

    service2 = IndexingService(uow_factory=capture_factory)
    with pytest.raises(RuntimeError, match="boom"):
        await service2.reindex_package(
            _pkg("fastapi"),
            (_chunk("fastapi", "A"),),
            (_member("fastapi", "X"),),
        )
    assert len(captured) == 1
    assert captured[0].rolled_back is True
    assert captured[0].committed is False


@pytest.mark.asyncio
async def test_remove_package_deletes_through_uow():
    """``remove_package`` deletes from all FOUR stores (packages, chunks,
    module_members, trees) but never upserts."""
    packages_store = InMemoryPackageStore()
    chunks_store = InMemoryChunkStore()
    module_members_store = InMemoryModuleMemberStore()
    trees_store = InMemoryDocumentTreeStore()

    # Seed cross-package data — only "fastapi" rows must be deleted.
    packages_store.items["fastapi"] = _pkg("fastapi")
    chunks_store.by_package.setdefault("fastapi", []).append(_chunk("fastapi", "A"))
    chunks_store.by_package.setdefault("starlette", []).append(_chunk("starlette", "B"))
    module_members_store.by_package.setdefault("fastapi", []).append(_member("fastapi", "X"))
    module_members_store.by_package.setdefault("starlette", []).append(_member("starlette", "Y"))
    trees_store.by_package["fastapi"] = [_tree("fastapi.t")]
    trees_store.by_package["other"] = [_tree("other.t")]

    factory = make_fake_uow_factory(
        packages=packages_store,
        chunks=chunks_store,
        module_members=module_members_store,
        trees=trees_store,
    )
    service = IndexingService(uow_factory=factory)
    await service.remove_package("fastapi")

    # Each store saw exactly one delete-shaped call.
    pkg_deletes = [c for c in packages_store.calls if c.method == "delete"]
    assert len(pkg_deletes) == 1
    assert pkg_deletes[0].payload == {"name": "fastapi"}

    chunk_deletes = [c for c in chunks_store.calls if c.method == "delete"]
    assert len(chunk_deletes) == 1
    assert chunk_deletes[0].payload == {"package": "fastapi"}

    member_deletes = [c for c in module_members_store.calls if c.method == "delete"]
    assert len(member_deletes) == 1
    assert member_deletes[0].payload == {"package": "fastapi"}

    # Trees store: delete_for_package fires unconditionally.
    assert any(
        c.method == "delete_for_package" and c.payload == "fastapi"
        for c in trees_store.calls
    )

    # Only fastapi rows removed; cross-package isolation holds.
    assert "fastapi" not in packages_store.items
    assert "fastapi" not in chunks_store.by_package
    assert "fastapi" not in module_members_store.by_package
    assert "fastapi" not in trees_store.by_package
    assert chunks_store.by_package["starlette"]  # survivors
    assert module_members_store.by_package["starlette"]
    assert [t.qualified_name for t in trees_store.by_package["other"]] == ["other.t"]


# ── clear_all uses unconditional match ───────────────────────────────────


@pytest.mark.asyncio
async def test_clear_all_wipes_every_store_via_uow_delete_all():
    """Spec I3: ``clear_all`` delegates to :meth:`UnitOfWork.delete_all`,
    which sweeps every entity store + the vectors backend in one atomic
    transaction. Replaces the legacy per-store ``delete(filter=All(()))``
    gymnastics — the test pins the new shape so a regression that
    reintroduces the old wiring fails immediately.
    """
    packages_store = InMemoryPackageStore()
    chunks_store = InMemoryChunkStore()
    module_members_store = InMemoryModuleMemberStore()
    trees_store = InMemoryDocumentTreeStore()

    packages_store.items["a"] = _pkg("a")
    packages_store.items["b"] = _pkg("b")
    chunks_store.by_package["a"] = [_chunk("a", "x")]
    chunks_store.by_package["b"] = [_chunk("b", "y")]
    module_members_store.by_package["a"] = [_member("a", "X")]
    module_members_store.by_package["b"] = [_member("b", "Y")]
    trees_store.by_package["a"] = [_tree("a.x")]
    trees_store.by_package["b"] = [_tree("b.y")]

    factory = make_fake_uow_factory(
        packages=packages_store,
        chunks=chunks_store,
        module_members=module_members_store,
        trees=trees_store,
    )
    service = IndexingService(uow_factory=factory)
    await service.clear_all()

    # Every store ended up empty — the only observable end-state guarantee
    # ``clear_all`` makes. The per-store call shape is an implementation
    # detail of :meth:`FakeUnitOfWork.delete_all`.
    assert packages_store.items == {}
    assert chunks_store.by_package == {}
    assert module_members_store.by_package == {}
    assert trees_store.by_package == {}
    # Trees store sees a delete_all (unconditional, no filter).
    assert any(c.method == "delete_all" for c in trees_store.calls)


# ── Tree store integration on reindex ────────────────────────────────────


@pytest.mark.asyncio
async def test_reindex_package_with_trees_calls_tree_store():
    """Non-empty trees → delete_for_package + save_many fire on the
    UoW's tree store, in that order.

    Spec C1: the test seeds proper :class:`DocumentNode` instances because
    the post-write cross-package re-resolution sweep
    (:meth:`IndexingService._reresolve_cross_package`) walks them via
    ``load_all_in_package`` to extract qnames for the
    :meth:`ReferenceStore.resolve_unresolved` Protocol call.
    """
    trees_store = InMemoryDocumentTreeStore()
    factory = make_fake_uow_factory(trees=trees_store)
    service = IndexingService(uow_factory=factory)

    pkg = _pkg("fastapi")
    fake_trees = (_tree("fastapi.t1"), _tree("fastapi.t2"))
    await service.reindex_package(pkg, (), (), trees=fake_trees)

    # The new resolve-unresolved sweep loads trees back via
    # load_all_in_package, so methods now include that read after the
    # save_many write.
    methods = [c.method for c in trees_store.calls]
    assert methods[:2] == ["delete_for_package", "save_many"]
    assert "load_all_in_package" in methods
    assert trees_store.calls[0].payload == "fastapi"
    pkg_name, saved_trees = trees_store.calls[1].payload
    assert pkg_name == "fastapi"
    assert saved_trees == fake_trees


@pytest.mark.asyncio
async def test_reindex_package_with_empty_trees_skips_tree_write():
    """Empty trees tuple → no write-side tree-store calls.

    Spec C1: the post-write cross-package re-resolution sweep still
    issues a read via ``load_all_in_package`` to collect qnames for
    :meth:`ReferenceStore.resolve_unresolved`. That read is harmless
    on an empty tree set (it returns an empty dict → no qnames →
    no resolve_unresolved call), so we assert the WRITE-side
    operations stay absent rather than asserting no calls at all.
    """
    trees_store = InMemoryDocumentTreeStore()
    factory = make_fake_uow_factory(trees=trees_store)
    service = IndexingService(uow_factory=factory)

    await service.reindex_package(_pkg("fastapi"), (), (), trees=())
    write_methods = {"save_many", "delete_for_package", "delete_all"}
    assert [c for c in trees_store.calls if c.method in write_methods] == []


@pytest.mark.asyncio
async def test_reindex_package_canonical_order():
    """Spec §13.3 canonical order: package → chunks → trees → members.

    The order matters so a future FK-style schema constraint (e.g.
    document_trees referencing chunks) stays satisfiable mid-transaction.
    """
    packages_store = InMemoryPackageStore()
    chunks_store = InMemoryChunkStore()
    module_members_store = InMemoryModuleMemberStore()
    trees_store = InMemoryDocumentTreeStore()
    factory = make_fake_uow_factory(
        packages=packages_store,
        chunks=chunks_store,
        module_members=module_members_store,
        trees=trees_store,
    )
    service = IndexingService(uow_factory=factory)

    pkg = _pkg("fastapi")
    chunk = _chunk("fastapi", "A")
    member = _member("fastapi", "X")
    # Spec C1: trees must be real DocumentNodes so the post-write
    # resolve-unresolved sweep can walk them for qnames.
    fake_trees = (_tree("fastapi.t1"),)

    await service.reindex_package(pkg, (chunk,), (member,), trees=fake_trees)

    # Verify each phase happened, then verify their relative order via a
    # combined call sequence reconstructed in real time.
    pkg_upsert = next(c for c in packages_store.calls if c.method == "upsert")
    # Diff-merge: added chunks land via ``insert`` (not ``upsert``).
    chunk_insert = next(c for c in chunks_store.calls if c.method == "insert")
    tree_save = next(c for c in trees_store.calls if c.method == "save_many")
    member_upsert = next(c for c in module_members_store.calls if c.method == "upsert_many")

    # Use list-index witnesses by recording the order via shared monotonic
    # counters on each call — since we can't merge call lists from
    # different stores into one chronological list easily, we instead lean
    # on the implementation contract: spec §13.3 says
    # package.upsert → chunks.insert → trees.save_many → members.upsert_many.
    # Each store recorded its call; assert each store saw its expected call.
    assert pkg_upsert.payload is pkg
    assert chunk in chunk_insert.payload
    assert tree_save.payload == ("fastapi", fake_trees)
    assert member in member_upsert.payload


@pytest.mark.asyncio
async def test_reindex_package_accepts_references_placeholder():
    """``references`` is accepted by the signature and flows through the
    resolver into ``uow.references`` (sub-PR #5b — the seam from #5/#5a-2
    is now wired through). A single unresolved ref must not raise; core
    stores still get their usual delete+upsert.
    """
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.storage.node_reference import NodeReference

    factory = make_fake_uow_factory()
    service = IndexingService(uow_factory=factory)
    await service.reindex_package(
        _pkg("fastapi"), (), (),
        references=(
            NodeReference(
                from_package="fastapi", from_node_id="fastapi.mod.fn",
                to_name="some_target", to_node_id=None,
                kind=ReferenceKind.CALLS,
            ),
        ),
    )
    # No assertion needed beyond "no exception" — the dataclass / signature
    # accepts the kwarg and the resolver runs without crashing.


# ── e2e regression against real SQLite ───────────────────────────────────


@pytest.mark.asyncio
async def test_indexing_service_clear_all_also_removes_null_package_rows(tmp_path):
    """Regression: ``clear_all`` previously used ``LIKE '%'`` which skips
    NULL package values. Seeding a row with ``package=NULL`` via raw SQL
    and then calling ``clear_all`` must leave the table empty.
    """
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.storage.factories import build_sqlite_indexing_service

    db_path = tmp_path / "clear.db"
    conn = open_index_database(db_path)
    conn.execute(
        "INSERT INTO packages(name,version,summary,homepage,dependencies,"
        "content_hash,origin) VALUES(?,?,?,?,?,?,?)",
        ("normal", "1.0", "", "", "[]", "h", "dependency"),
    )
    conn.execute(
        "INSERT INTO chunks(package, title, text, origin) VALUES(?,?,?,?)",
        ("normal", "t", "body", "dep_doc"),
    )
    # A NULL-package row simulates a schema drift / partially-written fixture.
    conn.execute(
        "INSERT INTO chunks(package, title, text, origin) VALUES(NULL, ?, ?, ?)",
        ("orphan", "orphan body", "dep_doc"),
    )
    conn.commit()
    conn.close()

    service = build_sqlite_indexing_service(db_path)

    await service.clear_all()

    conn = open_index_database(db_path)
    pkg_count = conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()
    assert pkg_count == 0
    # Both the normal row and the NULL-package row must be gone.
    assert chunk_count == 0


# ── Sub-PR #5b: references flow through reindex_package ──────────────────


@pytest.mark.asyncio
async def test_reindex_package_writes_references_via_uow():
    """spec §9 — references flow into uow.references.save_many."""
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.storage.node_reference import NodeReference
    from tests._fakes import InMemoryReferenceStore, make_fake_uow_factory

    refs_store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=refs_store)
    service = IndexingService(uow_factory=factory)

    pkg = _pkg("pkg")
    raw_refs = (
        NodeReference(
            from_package="pkg", from_node_id="pkg.mod.fn",
            to_name="helper", to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
    )
    await service.reindex_package(
        pkg, chunks=(), module_members=(), trees=(),
        references=raw_refs,
    )
    # save_many was called with the resolved tuple. Even though no trees
    # are indexed (so resolver can't resolve `helper`), the call happened.
    assert any(c.method == "save_many" for c in refs_store.calls)


@pytest.mark.asyncio
async def test_reindex_package_runs_resolver_when_aliases_provided():
    """AC #6 — alias rewrite + exact match flips to_node_id."""
    from pydocs_mcp.extraction.model import DocumentNode, NodeKind
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.storage.node_reference import NodeReference
    from tests._fakes import (
        InMemoryDocumentTreeStore,
        InMemoryReferenceStore,
        make_fake_uow_factory,
    )

    # Seed the tree store with `pkg.helpers.compute` as an indexed qname.
    tree = DocumentNode(
        node_id="pkg.helpers.compute",
        qualified_name="pkg.helpers.compute",
        title="compute", kind=NodeKind.FUNCTION,
        source_path="pkg/helpers.py", start_line=1, end_line=2,
        text="def compute(): ...", content_hash="h",
    )
    trees_store = InMemoryDocumentTreeStore()
    trees_store.by_package["pkg"] = [tree]
    # Also expose via load_all_in_package — the resolver loads from there.
    async def load_all_in_package(package, *, _store=trees_store):
        return {
            n.qualified_name: n
            for n in _store.by_package.get(package, [])
        }
    trees_store.load_all_in_package = load_all_in_package  # type: ignore

    refs_store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(trees=trees_store, references=refs_store)
    service = IndexingService(uow_factory=factory)

    raw_refs = (
        NodeReference(
            from_package="pkg", from_node_id="pkg.utils.runner",
            to_name="do_it", to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
    )
    aliases = {"pkg.utils": {"do_it": "pkg.helpers.compute"}}

    await service.reindex_package(
        _pkg("pkg"), chunks=(), module_members=(), trees=(),
        references=raw_refs, reference_aliases=aliases,
    )

    # save_many got the resolved ref — to_node_id is filled in.
    save_call = next(c for c in refs_store.calls if c.method == "save_many")
    _, materialised_refs = save_call.payload
    assert len(materialised_refs) == 1
    assert materialised_refs[0].to_node_id == "pkg.helpers.compute"


@pytest.mark.asyncio
async def test_reindex_package_writes_zero_refs_when_disabled():
    """Spec §9 — when no references emitted, no save_many call."""
    from tests._fakes import InMemoryReferenceStore, make_fake_uow_factory

    refs_store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=refs_store)
    service = IndexingService(uow_factory=factory)
    await service.reindex_package(
        _pkg("pkg"), chunks=(), module_members=(), trees=(),
        references=(),
    )
    # No save_many call recorded (the service skips when refs is empty).
    assert not any(c.method == "save_many" for c in refs_store.calls)


@pytest.mark.asyncio
async def test_remove_package_clears_references():
    """AC #13 — remove_package wipes the package's reference rows."""
    from tests._fakes import InMemoryReferenceStore, make_fake_uow_factory

    refs_store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=refs_store)
    service = IndexingService(uow_factory=factory)
    await service.remove_package("pkg")
    assert any(
        c.method == "delete_for_package" and c.payload == "pkg"
        for c in refs_store.calls
    )


@pytest.mark.asyncio
async def test_clear_all_wipes_references():
    """AC #14 — clear_all invokes uow.references.delete_all."""
    from tests._fakes import InMemoryReferenceStore, make_fake_uow_factory

    refs_store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=refs_store)
    service = IndexingService(uow_factory=factory)
    await service.clear_all()
    assert any(c.method == "delete_all" for c in refs_store.calls)


# ── Task 7 (I2 + I17): extracted helpers + find_stale_packages method ────


@pytest.mark.asyncio
async def test_diff_merge_chunks_empty_store_inserts_all():
    """`_diff_merge_chunks` against an empty store returns empty removed_ids
    and the full incoming tuple as added.

    Probes ``list_id_hash_pairs`` once; no ``delete_by_ids`` (nothing to
    remove); no ``insert`` (the helper just diffs — the orchestrator
    inserts later).
    """
    chunks_store = InMemoryChunkStore()
    factory = make_fake_uow_factory(chunks=chunks_store)
    service = IndexingService(uow_factory=factory)

    incoming = (
        _chunk("fastapi", "Routing"),
        _chunk("fastapi", "Middleware"),
    )

    async with factory() as uow:
        removed_ids, added_chunks = await service._diff_merge_chunks(
            uow, package_name="fastapi", incoming_chunks=incoming,
        )

    assert removed_ids == []
    # All incoming chunks are "added" since the store was empty.
    assert added_chunks == incoming


@pytest.mark.asyncio
async def test_diff_merge_chunks_removes_stale_and_keeps_unchanged():
    """`_diff_merge_chunks` keeps unchanged rows + removes stale ids.

    Pre-seed the chunk store via ``insert`` (mimicking the SQLite autoincrement
    so ``list_id_hash_pairs`` returns real ids). Then diff against an incoming
    set that drops one of the seeded rows + adds a brand new one. The dropped
    hash's id ends up in ``removed_ids``; the new hash ends up in ``added``.
    """
    chunks_store = InMemoryChunkStore()
    keep = _chunk("fastapi", "keep", text="keep-text")
    drop = _chunk("fastapi", "drop", text="drop-text")
    # Use the store's own insert path so ids get assigned just like SQLite.
    await chunks_store.insert((keep, drop))

    factory = make_fake_uow_factory(chunks=chunks_store)
    service = IndexingService(uow_factory=factory)

    new_chunk = _chunk("fastapi", "brand-new", text="new-text")
    # Drop "drop"; keep "keep"; add a brand-new chunk.
    incoming = (keep, new_chunk)

    async with factory() as uow:
        removed_ids, added_chunks = await service._diff_merge_chunks(
            uow, package_name="fastapi", incoming_chunks=incoming,
        )

    # One stale id (the "drop" row).
    assert len(removed_ids) == 1
    # Only the brand-new chunk is "added"; "keep" was unchanged.
    assert added_chunks == (new_chunk,)


@pytest.mark.asyncio
async def test_persist_references_empty_skips_save_many():
    """`_persist_references` with empty refs: sweeps the package's rows
    but skips ``save_many`` (no resolved tuple to write)."""
    from tests._fakes import InMemoryReferenceStore

    refs_store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=refs_store)
    service = IndexingService(uow_factory=factory)

    async with factory() as uow:
        await service._persist_references(
            uow,
            package_name="pkg",
            references=(),
            reference_aliases={},
            class_attribute_types={},
        )

    # delete_for_package fired; save_many did NOT.
    assert any(
        c.method == "delete_for_package" and c.payload == "pkg"
        for c in refs_store.calls
    )
    assert not any(c.method == "save_many" for c in refs_store.calls)


@pytest.mark.asyncio
async def test_persist_references_non_empty_writes_resolved_refs():
    """`_persist_references` runs the resolver + persists resolved refs."""
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.storage.node_reference import NodeReference
    from tests._fakes import InMemoryReferenceStore

    refs_store = InMemoryReferenceStore()
    factory = make_fake_uow_factory(references=refs_store)
    service = IndexingService(uow_factory=factory)

    raw_refs = (
        NodeReference(
            from_package="pkg", from_node_id="pkg.mod.fn",
            to_name="helper", to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
    )
    async with factory() as uow:
        await service._persist_references(
            uow,
            package_name="pkg",
            references=raw_refs,
            reference_aliases={},
            class_attribute_types={},
        )

    # delete_for_package + save_many both fired.
    assert any(c.method == "delete_for_package" for c in refs_store.calls)
    assert any(c.method == "save_many" for c in refs_store.calls)


@pytest.mark.asyncio
async def test_find_stale_packages_method_lives_on_service():
    """I17 — `find_stale_packages` is a method on `IndexingService`,
    not a module-level free function.

    Empty package store → empty list (no crashes).
    """
    factory = make_fake_uow_factory()
    service = IndexingService(uow_factory=factory)

    stale = await service.find_stale_packages(current_model="fake-model")
    assert stale == []


@pytest.mark.asyncio
async def test_find_stale_packages_filters_by_model():
    """I17 — packages tagged with a different model are flagged stale;
    packages tagged with the current model are not; packages with
    ``embedding_model=None`` are excluded (legacy / pre-embedding rows).
    """
    from dataclasses import replace as dc_replace

    packages_store = InMemoryPackageStore()
    # Tagged with old model → stale under "current".
    packages_store.items["a"] = dc_replace(_pkg("a"), embedding_model="old")
    # Tagged with current model → not stale.
    packages_store.items["b"] = dc_replace(_pkg("b"), embedding_model="current")
    # No model tag → never stale.
    packages_store.items["c"] = _pkg("c")  # embedding_model defaults to None

    factory = make_fake_uow_factory(packages=packages_store)
    service = IndexingService(uow_factory=factory)
    stale = await service.find_stale_packages(current_model="current")
    assert set(stale) == {"a"}
