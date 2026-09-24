"""Membership swap, extraction cache, project GC, and their wiring (spec §6.1, §6.3)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_mcp.application.branch_manifest import (
    BranchManifest,
    NoBranchManifestBuilder,
    WorkingTreeManifestBuilder,
)
from pydocs_mcp.application.branch_membership import (
    collect_project_garbage,
    extraction_rows,
    membership_rows,
    write_branch_membership,
    write_file_extraction_cache,
)
from pydocs_mcp.application.branch_retirement import set_pinned
from pydocs_mcp.application.indexing_service import ChunkDiffOutcome, IndexingService
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchIndexSource,
    Chunk,
    ModuleMember,
    Package,
    PackageOrigin,
)
from pydocs_mcp.storage.branch_records import BranchFile, ChunkMembership, FileExtraction
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import (
    FakeGitRepository,
    InMemoryChunkStore,
    SpyVectorStore,
    make_fake_uow_factory,
)


def _chunk(
    title: str, path: str, start: int, end: int, package: str = PROJECT_PACKAGE_NAME
) -> Chunk:
    return Chunk.from_test_inputs(
        package=package,
        module=path.replace("/", ".").removesuffix(".py"),
        title=title,
        text=title,
        metadata={"source_path": path, "start_line": start, "end_line": end},
    )


def _package(name: str = PROJECT_PACKAGE_NAME, origin: PackageOrigin = PackageOrigin.PROJECT):
    return Package(
        name=name,
        version="0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=origin,
    )


# A key in the shape ``file_extraction_cache_key`` makes (the manifest refuses
# any other shape).
_KEY = "p|x:k"


def _manifest(
    name: str = "main",
    files=(("pkg/a.py", "blob-a"), ("pkg/b.py", "blob-b")),
    key: str = _KEY,
) -> BranchManifest:
    return BranchManifest(
        name=name,
        head_sha="c" * 40,
        source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p",
        files=tuple(BranchFile(branch=name, path=p, blob_sha=b) for p, b in files),
        worktree_path="/repo",
        extraction_cache_key=key,
    )


def test_membership_rows_carry_per_branch_spans() -> None:
    rows = membership_rows(_manifest(), ((_chunk("t", "pkg/a.py", 3, 9), 41),))
    assert [(r.branch, r.chunk_id, r.source_path, r.start_line, r.end_line) for r in rows] == [
        ("main", 41, "pkg/a.py", 3, 9)
    ]


def test_extraction_rows_group_spans_per_blob_and_skip_blank_blobs() -> None:
    manifest = _manifest(files=(("pkg/a.py", "blob-a"), ("pkg/n.py", "")))
    rows = extraction_rows(
        manifest,
        (
            (_chunk("x", "pkg/a.py", 1, 2), 1),
            (_chunk("y", "pkg/a.py", 3, 4), 2),
            (_chunk("z", "pkg/n.py", 1, 1), 3),
        ),
        now=7.0,
    )
    assert len(rows) == 1
    # Keyed by the extraction key (#309), not by the manifest's bare pipeline hash.
    assert (rows[0].blob_sha, rows[0].path, rows[0].pipeline_hash, rows[0].created_at) == (
        "blob-a",
        "pkg/a.py",
        _KEY,
        7.0,
    )
    assert json.loads(rows[0].chunk_spans) == [[1, 1, 2], [2, 3, 4]]


def test_extraction_rows_emit_spans_in_file_order() -> None:
    """``FileExtraction.chunk_spans`` is documented — and read by P1 — as file
    order, but ``assignments`` arrives kept-then-added (diff order). A span with
    no start line has no place in that order and sorts last, deterministically.
    """
    no_start = Chunk.from_test_inputs(
        package=PROJECT_PACKAGE_NAME,
        module="pkg.a",
        title="late",
        text="late",
        metadata={"source_path": "pkg/a.py", "start_line": None, "end_line": 1},
    )
    rows = extraction_rows(
        _manifest(),
        (
            (_chunk("kept", "pkg/a.py", 30, 40), 7),
            (no_start, 9),
            (_chunk("added", "pkg/a.py", 5, 9), 8),
        ),
        now=7.0,
    )
    assert json.loads(rows[0].chunk_spans) == [[8, 5, 9], [7, 30, 40], [9, None, 1]]


async def test_write_branch_membership_replaces_the_previous_working_tree_branch() -> None:
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await write_branch_membership(uow, manifest=_manifest("old"), assignments=(), now=1.0)
        await write_branch_membership(
            uow,
            manifest=_manifest("main"),
            assignments=((_chunk("t", "pkg/a.py", 1, 2), 5),),
            now=2.0,
        )
        assert [b.name for b in await uow.branches.list_branches()] == ["main"]
        assert await uow.branches.default_branch_name() == "main"
        assert await uow.branch_chunks.count_for_branch("old") == 0
        assert [m.chunk_id for m in await uow.branch_chunks.list_membership("main")] == [5]
        await uow.commit()


async def test_write_branch_membership_stamps_the_manifest_base() -> None:
    """#308: the branch row carries its base name and merge-base after the pass."""
    manifest = replace(_manifest("feature/x"), base_name="main", merge_base_sha="e" * 40)
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await write_branch_membership(uow, manifest=manifest, assignments=(), now=1.0)
        record = await uow.branches.get_branch("feature/x")
    assert record is not None
    assert (record.base_name, record.merge_base_sha) == ("main", "e" * 40)


async def test_a_re_stamp_keeps_the_operators_pin() -> None:
    """#316: ``branches --pin`` on the checked-out branch must survive the next
    pass — watch mode re-stamps on every save."""
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await write_branch_membership(uow, manifest=_manifest("main"), assignments=(), now=1.0)
        await set_pinned(uow, "main", True)
        await write_branch_membership(uow, manifest=_manifest("main"), assignments=(), now=2.0)
        record = await uow.branches.get_branch("main")
    assert record is not None and record.pinned is True and record.indexed_at == 2.0


async def test_reindex_project_package_writes_membership_cache_and_collects_garbage() -> None:
    chunks_store = InMemoryChunkStore()
    vectors = SpyVectorStore()
    factory = make_fake_uow_factory(chunks=chunks_store, vectors=vectors)
    service = IndexingService(uow_factory=factory)
    keep, drop = _chunk("keep", "pkg/a.py", 1, 2), _chunk("drop", "pkg/b.py", 1, 2)
    await service.reindex_package(_package(), (keep, drop), (), branch_manifest=_manifest())
    drop_id = next(c.id for c in chunks_store.by_package[PROJECT_PACKAGE_NAME] if c.text == "drop")
    new = _chunk("new", "pkg/b.py", 1, 3)
    await service.reindex_package(_package(), (keep, new), (), branch_manifest=_manifest())
    async with factory() as uow:
        rows = await uow.branch_chunks.list_membership("main")
        assert sorted(m.source_path for m in rows) == ["pkg/a.py", "pkg/b.py"]
        assert await uow.file_extractions.get("blob-a", "pkg/a.py", _KEY) is not None
        assert await uow.file_extractions.get("blob-b", "pkg/b.py", _KEY) is not None
        assert {c.text for c in chunks_store.by_package[PROJECT_PACKAGE_NAME]} == {"keep", "new"}
    assert drop_id in vectors.removed  # the orphan's vector was dropped by the GC path


def _tree(module: str, path: str) -> DocumentNode:
    return DocumentNode(module, module, module, NodeKind.MODULE, path, 1, 9, "t", "h")


async def test_reindex_caches_each_file_s_tree_members_and_unresolved_sweep() -> None:
    """#309: the working-tree pass POPULATES the cache, so a later branch
    carrying the same blob copies these instead of parsing the file."""
    factory = make_fake_uow_factory()
    service = IndexingService(uow_factory=factory)
    member = ModuleMember(
        metadata={"package": PROJECT_PACKAGE_NAME, "module": "pkg.a", "name": "f", "kind": "def"}
    )
    ref = NodeReference(PROJECT_PACKAGE_NAME, "pkg.a.f", "pkg.b.g", None, ReferenceKind.CALLS)
    await service.reindex_package(
        _package(),
        (_chunk("t", "pkg/a.py", 1, 2),),
        (member,),
        trees=(_tree("pkg.a", "pkg/a.py"), _tree("pkg.b", "pkg/b.py")),
        references=(ref,),
        reference_aliases={"pkg.a": {"g": "pkg.b.g"}},
        branch_manifest=_manifest(),
    )
    async with factory() as uow:
        row = await uow.file_extractions.get("blob-a", "pkg/a.py", _KEY)
        tree_only = await uow.file_extractions.get("blob-b", "pkg/b.py", _KEY)
    assert row is not None and tree_only is not None
    assert json.loads(row.tree_json)["qualified_name"] == "pkg.a"
    assert [m["name"] for m in json.loads(row.members_json)] == ["f"]
    sweep = json.loads(row.references_json)
    assert sweep["aliases"] == {"pkg.a": {"g": "pkg.b.g"}}
    assert [r["to_name"] for r in sweep["refs"]] == ["pkg.b.g"]
    assert "to_node_id" not in sweep["refs"][0]  # resolution reruns per branch
    assert tree_only.chunk_spans == "[]" and tree_only.tree_json is not None


async def test_a_pass_under_a_new_extraction_key_supersedes_the_old_rows() -> None:
    """A grammar, chunker or capture change moves the key; the old rows can
    never hit again and must not accumulate (#261)."""
    factory = make_fake_uow_factory()
    service = IndexingService(uow_factory=factory)
    chunk = _chunk("t", "pkg/a.py", 1, 2)
    await service.reindex_package(_package(), (chunk,), (), branch_manifest=_manifest(key="p|x:1"))
    await service.reindex_package(_package(), (chunk,), (), branch_manifest=_manifest(key="p|x:2"))
    async with factory() as uow:
        assert await uow.file_extractions.get("blob-a", "pkg/a.py", "p|x:1") is None
        assert await uow.file_extractions.get("blob-a", "pkg/a.py", "p|x:2") is not None


async def test_collect_project_garbage_drops_superseded_and_unreferenced_rows() -> None:
    factory = make_fake_uow_factory()
    live = FileExtraction("blob-a", "pkg/a.py", "p|x:new", "[]", 1.0)
    superseded = replace(live, pipeline_hash="p|x:old")
    unreferenced = FileExtraction("blob-z", "pkg/z.py", "p|x:new", "[]", 1.0)
    async with factory() as uow:
        await write_branch_membership(uow, manifest=_manifest(), assignments=(), now=1.0)
        await uow.file_extractions.upsert_many([live, superseded, unreferenced])
        # A purge knows no current key: it says so, and the keys stay untouched.
        await collect_project_garbage(uow, extraction_cache_key=None)
        assert await uow.file_extractions.get("blob-a", "pkg/a.py", "p|x:old") == superseded
        assert await uow.file_extractions.get("blob-z", "pkg/z.py", "p|x:new") is None
        await collect_project_garbage(uow, extraction_cache_key="p|x:new")
        assert await uow.file_extractions.get("blob-a", "pkg/a.py", "p|x:old") is None
        assert await uow.file_extractions.get("blob-a", "pkg/a.py", "p|x:new") == live


async def test_collect_project_garbage_must_be_told_the_pass_key() -> None:
    """Forgotten wiring fails loudly (#309 review): a pass that left the key
    out would never sweep superseded rows, and a malformed key would sweep
    every row as superseded."""
    async with make_fake_uow_factory()() as uow:
        with pytest.raises(TypeError, match="extraction_cache_key"):
            await collect_project_garbage(uow)  # type: ignore[call-arg]
        with pytest.raises(ValueError, match="got 'p'"):
            await collect_project_garbage(uow, extraction_cache_key="p")


async def test_collect_project_garbage_drops_the_freed_chunks_vectors() -> None:
    """The GC owns the vector cleanup (#307), so no caller — the stamp, the
    per-branch purge, a later branch pass — can free a chunk and leave its
    vector behind under a rowid SQLite reuses."""
    vectors = SpyVectorStore()
    factory = make_fake_uow_factory(vectors=vectors)
    async with factory() as uow:
        kept, orphan = await uow.chunks.insert_returning_ids(
            (_chunk("kept", "pkg/a.py", 1, 1), _chunk("orphan", "pkg/b.py", 1, 1))
        )
        await uow.branch_chunks.replace_membership("main", [ChunkMembership("main", kept, "a")])
        freed = await collect_project_garbage(uow, extraction_cache_key=None)
        # nothing left to free
        assert await collect_project_garbage(uow, extraction_cache_key=None) == ()
    assert freed == (orphan,)
    assert vectors.removed == [orphan]


async def test_collect_project_garbage_drops_cache_rows_naming_a_freed_chunk() -> None:
    """#310: a cache row whose ``(blob, path)`` another manifest still lists
    survives the unreferenced sweep, so it must go with the ids it names —
    else a later hit restores membership over deleted (or reused) rowids."""
    factory = make_fake_uow_factory()
    async with factory() as uow:
        kept, freed_id = await uow.chunks.insert_returning_ids(
            (_chunk("kept", "pkg/a.py", 1, 1), _chunk("freed", "pkg/a.py", 2, 2))
        )
        await write_branch_membership(uow, manifest=_manifest(), assignments=(), now=1.0)
        await uow.branch_chunks.replace_membership("main", [ChunkMembership("main", kept, "a")])
        live = FileExtraction("blob-b", "pkg/b.py", _KEY, f"[[{kept}, 1, 1]]", 1.0)
        stale = FileExtraction("blob-a", "pkg/a.py", _KEY, f"[[{freed_id}, 2, 2]]", 1.0)
        await uow.file_extractions.upsert_many([live, stale])
        assert await collect_project_garbage(uow, extraction_cache_key=_KEY) == (freed_id,)
        assert await uow.file_extractions.get("blob-a", "pkg/a.py", _KEY) is None
        assert await uow.file_extractions.get("blob-b", "pkg/b.py", _KEY) == live


async def test_dependency_package_keeps_direct_removal() -> None:
    chunks_store = InMemoryChunkStore()
    factory = make_fake_uow_factory(chunks=chunks_store)
    service = IndexingService(uow_factory=factory)
    dep = _package("requests", PackageOrigin.DEPENDENCY)
    a, b = _chunk("a", "r/a.py", 1, 1, "requests"), _chunk("b", "r/b.py", 1, 1, "requests")
    await service.reindex_package(dep, (a, b), ())
    await service.reindex_package(dep, (a,), ())
    assert [c.text for c in chunks_store.by_package["requests"]] == ["a"]
    assert any(call.method == "delete_by_ids" for call in chunks_store.calls)


async def test_project_package_without_manifest_keeps_legacy_removal() -> None:
    """The manifest-less reindex deletes removed rows directly AND sweeps the
    membership they left behind (R12).

    Seeded WITH a manifest so membership exists, then reindexed WITHOUT one —
    exactly the case the sweep exists for. A stale membership row would later
    alias a brand-new chunk into this branch, because the v16 tables carry no
    foreign keys and SQLite reuses freed rowids.
    """
    chunks_store = InMemoryChunkStore()
    factory = make_fake_uow_factory(chunks=chunks_store)
    service = IndexingService(uow_factory=factory)
    a, b = _chunk("a", "pkg/a.py", 1, 1), _chunk("b", "pkg/b.py", 1, 1)
    await service.reindex_package(_package(), (a, b), (), branch_manifest=_manifest())
    by_text = {c.text: c.id for c in chunks_store.by_package[PROJECT_PACKAGE_NAME]}
    await service.reindex_package(_package(), (a,), ())
    assert [c.text for c in chunks_store.by_package[PROJECT_PACKAGE_NAME]] == ["a"]
    async with factory() as uow:
        live = [m.chunk_id for m in await uow.branch_chunks.list_membership("main")]
    # Targeted sweep, not a wipe: the dropped chunk's row is gone, the kept
    # chunk's row survives (no stamp ran — this pass had no manifest).
    assert by_text["b"] not in live
    assert live == [by_text["a"]]


async def test_diff_outcome_reports_kept_assignments() -> None:
    chunks_store = InMemoryChunkStore()
    factory = make_fake_uow_factory(chunks=chunks_store)
    service = IndexingService(uow_factory=factory)
    kept = _chunk("kept", "pkg/a.py", 1, 1)
    await service.reindex_package(_package(), (kept,), ())
    async with factory() as uow:
        outcome = await service._diff_merge_chunks(
            uow,
            package_name=PROJECT_PACKAGE_NAME,
            incoming_chunks=(kept, _chunk("n", "pkg/n.py", 1, 1)),
        )
    assert isinstance(outcome, ChunkDiffOutcome)
    assert [c.text for c, _ in outcome.kept_assignments] == ["kept"]
    assert [c.text for c in outcome.added_chunks] == ["n"] and outcome.removed_ids == ()


async def test_remove_project_package_drops_branch_rows() -> None:
    factory = make_fake_uow_factory()
    service = IndexingService(uow_factory=factory)
    await service.reindex_package(
        _package(), (_chunk("t", "pkg/a.py", 1, 1),), (), branch_manifest=_manifest()
    )
    await service.remove_package(PROJECT_PACKAGE_NAME)
    async with factory() as uow:
        assert await uow.branches.list_branches() == ()
        assert await uow.branch_chunks.count_for_branch("main") == 0
        assert await collect_project_garbage(uow, extraction_cache_key=None) == ()


async def test_remove_dependency_package_sweeps_only_its_own_membership_rows() -> None:
    """R12 at the ``remove_package`` site, isolated from ``drop_all_branches``.

    ``drop_all_branches`` runs only for the project package, so removing a
    DEPENDENCY is the one path where the id-targeted sweep is the sole thing
    that can clear a membership row — deleting the ``delete_for_chunk_ids``
    call leaves the dependency's row pointing at a freed rowid.
    """
    chunks_store = InMemoryChunkStore()
    factory = make_fake_uow_factory(chunks=chunks_store)
    service = IndexingService(uow_factory=factory)
    await service.reindex_package(
        _package(), (_chunk("p", "pkg/a.py", 1, 1),), (), branch_manifest=_manifest()
    )
    dep = _package("requests", PackageOrigin.DEPENDENCY)
    await service.reindex_package(dep, (_chunk("a", "r/a.py", 1, 1, "requests"),), ())
    dep_id = chunks_store.by_package["requests"][0].id
    project_id = chunks_store.by_package[PROJECT_PACKAGE_NAME][0].id
    # Seed the dependency chunk INTO the branch's membership by hand: the
    # service never stamps a dependency, so this is the only way to reach the
    # invariant "no membership row outlives the chunk it points at".
    async with factory() as uow:
        rows = await uow.branch_chunks.list_membership("main")
        await uow.branch_chunks.replace_membership(
            "main", (*rows, ChunkMembership("main", dep_id, "r/a.py", 1, 1))
        )
        await uow.commit()

    await service.remove_package("requests")

    async with factory() as uow:
        live = [m.chunk_id for m in await uow.branch_chunks.list_membership("main")]
    assert dep_id not in live  # swept by delete_for_chunk_ids
    assert live == [project_id]  # drop_all_branches did NOT run (not the project package)


async def test_dependency_package_with_a_manifest_never_stamps_the_branch() -> None:
    """R15: the stamp is gated on the PROJECT origin, not on the manifest alone.

    A manifest reaching a dependency would overwrite the branch's membership
    with that dependency's chunks, and the project GC in the same transaction
    would then delete every project chunk — silent data loss.
    """
    chunks_store = InMemoryChunkStore()
    factory = make_fake_uow_factory(chunks=chunks_store)
    service = IndexingService(uow_factory=factory)
    await service.reindex_package(
        _package(), (_chunk("p", "pkg/a.py", 1, 1),), (), branch_manifest=_manifest()
    )
    dep = _package("requests", PackageOrigin.DEPENDENCY)
    a, b = _chunk("a", "r/a.py", 1, 1, "requests"), _chunk("b", "r/b.py", 1, 1, "requests")
    await service.reindex_package(dep, (a, b), (), branch_manifest=_manifest())
    await service.reindex_package(dep, (a,), (), branch_manifest=_manifest())

    # The dependency took the legacy path (its dropped chunk is gone) ...
    assert [c.text for c in chunks_store.by_package["requests"]] == ["a"]
    # ... and the project's branch membership + chunks are untouched.
    async with factory() as uow:
        rows = await uow.branch_chunks.list_membership("main")
    assert [m.source_path for m in rows] == ["pkg/a.py"]
    assert [c.text for c in chunks_store.by_package[PROJECT_PACKAGE_NAME]] == ["p"]


def test_project_indexer_default_builder_is_the_null_object() -> None:
    from pydocs_mcp.application.project_indexer import ProjectIndexer

    assert (
        ProjectIndexer.__dataclass_fields__["manifest_builder"].default_factory
        is NoBranchManifestBuilder
    )


def test_factory_wires_the_working_tree_builder(tmp_path: Path) -> None:
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.factories import build_project_indexer

    db = tmp_path / "p.db"
    open_index_database(db).close()
    bundle = build_project_indexer(AppConfig.load(), db, use_inspect=False, inspect_depth=None)
    assert isinstance(bundle.orchestrator.manifest_builder, WorkingTreeManifestBuilder)
    assert bundle.orchestrator.manifest_builder.pipeline_hash == bundle.pipeline_hash


def test_factory_keys_the_extraction_cache_with_the_settings_the_pipeline_uses(
    tmp_path: Path,
) -> None:
    """The builder keys the cache with the SAME chunking and capture settings
    ``ContentHashStage`` folds (``app_config.extraction.chunking`` and
    ``reference_graph.capture``), so a non-stock knob reaches the key (#309)."""
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.factories import build_project_indexer

    db = tmp_path / "p.db"
    open_index_database(db).close()
    overlay = tmp_path / "config.yaml"
    overlay.write_text(
        "extraction:\n  chunking:\n    text_section:\n      window_lines: 7\n"
        "reference_graph:\n  capture:\n    kinds: [calls, mentions]\n",
        encoding="utf-8",
    )
    config = AppConfig.load(explicit_path=overlay)
    bundle = build_project_indexer(config, db, use_inspect=False, inspect_depth=None)
    builder = bundle.orchestrator.manifest_builder
    assert builder.chunking == config.extraction.chunking
    assert builder.reference_capture == config.reference_graph.capture
    assert builder.current_extraction_cache_key().startswith(f"{bundle.pipeline_hash}|x:")


def test_factory_wires_the_base_resolver_from_the_git_config(tmp_path: Path) -> None:
    """The composition root resolves the base with ``config.git``, not defaults."""
    from pydocs_mcp.application.branch_policy import BaseBranch
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.factories import build_project_indexer

    db = tmp_path / "p.db"
    open_index_database(db).close()
    overlay = tmp_path / "config.yaml"
    overlay.write_text("git:\n  branches:\n    base: develop\n", encoding="utf-8")
    config = AppConfig.load(explicit_path=overlay)
    bundle = build_project_indexer(config, db, use_inspect=False, inspect_depth=None)
    git = FakeGitRepository(refs={"refs/heads/develop": "a" * 40, "refs/heads/main": "b" * 40})
    resolver = bundle.orchestrator.manifest_builder.base_resolver
    assert resolver(git) == BaseBranch("develop", "a" * 40, None)
