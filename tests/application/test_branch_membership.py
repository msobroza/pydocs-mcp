"""Membership swap, extraction cache, project GC, and their wiring (spec §6.1, §6.3)."""

from __future__ import annotations

import json
from pathlib import Path

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
from pydocs_mcp.application.indexing_service import ChunkDiffOutcome, IndexingService
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchIndexSource,
    Chunk,
    Package,
    PackageOrigin,
)
from pydocs_mcp.storage.branch_records import BranchFile
from tests._fakes import InMemoryChunkStore, SpyVectorStore, make_fake_uow_factory


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


def _manifest(
    name: str = "main", files=(("pkg/a.py", "blob-a"), ("pkg/b.py", "blob-b"))
) -> BranchManifest:
    return BranchManifest(
        name=name,
        head_sha="c" * 40,
        source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p",
        files=tuple(BranchFile(branch=name, path=p, blob_sha=b) for p, b in files),
        worktree_path="/repo",
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
    assert (rows[0].blob_sha, rows[0].path, rows[0].pipeline_hash, rows[0].created_at) == (
        "blob-a",
        "pkg/a.py",
        "p",
        7.0,
    )
    assert json.loads(rows[0].chunk_spans) == [[1, 1, 2], [2, 3, 4]]


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
        assert await uow.file_extractions.get("blob-a", "pkg/a.py", "p") is not None
        assert await uow.file_extractions.get("blob-b", "pkg/b.py", "p") is not None
        assert {c.text for c in chunks_store.by_package[PROJECT_PACKAGE_NAME]} == {"keep", "new"}
    assert drop_id in vectors.removed  # the orphan's vector was dropped by the GC path


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
    chunks_store = InMemoryChunkStore()
    factory = make_fake_uow_factory(chunks=chunks_store)
    service = IndexingService(uow_factory=factory)
    a, b = _chunk("a", "pkg/a.py", 1, 1), _chunk("b", "pkg/b.py", 1, 1)
    await service.reindex_package(_package(), (a, b), ())
    await service.reindex_package(_package(), (a,), ())
    assert [c.text for c in chunks_store.by_package[PROJECT_PACKAGE_NAME]] == ["a"]


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
        assert await collect_project_garbage(uow) == ()


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
