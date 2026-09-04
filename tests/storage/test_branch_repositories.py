"""Branch stores (spec §6.1): SQLite round-trips, Protocol conformance, fake parity."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchIndexSource,
    BranchSlice,
    BranchStatus,
    Chunk,
    FileChangeKind,
)
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.storage.branch_records import (
    BranchFile,
    BranchRecord,
    ChunkMembership,
    FileExtraction,
)
from pydocs_mcp.storage.protocols import (
    BranchChunkStore,
    BranchStore,
    ChunkStore,
    FileExtractionStore,
    UnitOfWork,
)
from pydocs_mcp.storage.sqlite import (
    SqliteBranchChunkRepository,
    SqliteBranchRepository,
    SqliteChunkRepository,
    SqliteFileExtractionRepository,
    SqliteUnitOfWork,
)
from tests._fakes import (
    InMemoryBranchChunkStore,
    InMemoryBranchStore,
    InMemoryFileExtractionStore,
    make_fake_uow_factory,
)


def _record(name: str = "main", *, is_default: bool = True) -> BranchRecord:
    return BranchRecord(
        name=name,
        head_sha="a" * 40,
        source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p",
        indexed_at=10.0,
        last_used_at=10.0,
        is_default=is_default,
    )


@pytest.fixture
def uow_factory(tmp_path: Path):
    db = tmp_path / "b.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    return lambda: SqliteUnitOfWork(provider=provider)


def test_sqlite_repositories_conform(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    assert isinstance(SqliteBranchRepository(provider=provider), BranchStore)
    assert isinstance(SqliteBranchChunkRepository(provider=provider), BranchChunkStore)
    assert isinstance(SqliteFileExtractionRepository(provider=provider), FileExtractionStore)
    assert isinstance(SqliteChunkRepository(provider=provider), ChunkStore)
    assert isinstance(SqliteUnitOfWork(provider=provider), UnitOfWork)


def test_fakes_conform() -> None:
    assert isinstance(InMemoryBranchStore(), BranchStore)
    assert isinstance(InMemoryBranchChunkStore(), BranchChunkStore)
    assert isinstance(InMemoryFileExtractionStore(), FileExtractionStore)
    assert isinstance(make_fake_uow_factory()(), UnitOfWork)


@pytest.mark.parametrize("kind", ["sqlite", "fake"])
async def test_branch_and_files_round_trip(kind: str, uow_factory) -> None:
    factory = uow_factory if kind == "sqlite" else make_fake_uow_factory()
    async with factory() as uow:
        await uow.branches.upsert_branch(_record())
        await uow.branches.replace_files(
            "main",
            [
                BranchFile(branch="main", path="pkg/a.py", blob_sha="b1"),
                BranchFile(branch="main", path="pkg/b.py", blob_sha="b2"),
            ],
        )
        await uow.commit()
    async with factory() as uow:
        assert await uow.branches.get_branch("main") == _record()
        assert await uow.branches.default_branch_name() == "main"
        assert await uow.branches.count_files("main") == 2
        assert {f.path for f in await uow.branches.list_files("main")} == {"pkg/a.py", "pkg/b.py"}
        # replace is a swap, not an append
        await uow.branches.replace_files("main", [BranchFile("main", "pkg/a.py", "b9")])
        assert [f.blob_sha for f in await uow.branches.list_files("main")] == ["b9"]
        await uow.branches.delete_branch("main")
        assert await uow.branches.get_branch("main") is None
        assert await uow.branches.count_files("main") == 0
        await uow.commit()


@pytest.mark.parametrize("kind", ["sqlite", "fake"])
async def test_membership_round_trip_and_project_gc(kind: str, uow_factory) -> None:
    factory = uow_factory if kind == "sqlite" else make_fake_uow_factory()
    kept = Chunk.from_test_inputs(package=PROJECT_PACKAGE_NAME, module="m", title="k", text="k")
    orphan = Chunk.from_test_inputs(package=PROJECT_PACKAGE_NAME, module="m", title="o", text="o")
    dep = Chunk.from_test_inputs(package="requests", module="r", title="d", text="d")
    async with factory() as uow:
        ids = await uow.chunks.insert_returning_ids((kept, orphan, dep))
        assert len(ids) == 3 and len(set(ids)) == 3
        await uow.branch_chunks.replace_membership(
            "main",
            [
                ChunkMembership(
                    branch="main", chunk_id=ids[0], source_path="m.py", start_line=1, end_line=2
                ),
            ],
        )
        deleted = await uow.chunks.delete_unreferenced_project_chunks()
        await uow.commit()
    assert deleted == (ids[1],)  # the orphan project chunk only; the dependency chunk survives
    async with factory() as uow:
        rows = await uow.branch_chunks.list_membership("main")
        assert [(r.chunk_id, r.start_line, r.end_line) for r in rows] == [(ids[0], 1, 2)]
        assert await uow.branch_chunks.count_for_branch("main") == 1
        assert await uow.chunks.count(filter={"package": "requests"}) == 1
        assert await uow.chunks.count(filter={"package": PROJECT_PACKAGE_NAME}) == 1


@pytest.mark.parametrize("kind", ["sqlite", "fake"])
async def test_insert_returning_ids_follows_input_order(kind: str, uow_factory) -> None:
    """Callers pair ``chunks[i]`` with ``ids[i]`` to build membership rows, so a
    batch that interleaves packages must not come back in bucket order."""
    factory = uow_factory if kind == "sqlite" else make_fake_uow_factory()
    packages = (PROJECT_PACKAGE_NAME, "requests", PROJECT_PACKAGE_NAME)
    texts = ("first", "second", "third")
    batch = tuple(
        Chunk.from_test_inputs(package=p, module="m", title=t, text=t)
        for p, t in zip(packages, texts, strict=True)
    )
    async with factory() as uow:
        ids = await uow.chunks.insert_returning_ids(batch)
        await uow.commit()
    async with factory() as uow:
        stored = {c.id: c for c in await uow.chunks.list()}
    assert [stored[i].text for i in ids] == list(texts)


@pytest.mark.parametrize("kind", ["sqlite", "fake"])
async def test_delete_for_chunk_ids_drops_only_those_rows(kind: str, uow_factory) -> None:
    """Membership must never outlive the chunk it points at — SQLite reuses
    freed rowids, so a stale row would alias a future insert."""
    factory = uow_factory if kind == "sqlite" else make_fake_uow_factory()
    async with factory() as uow:
        await uow.branch_chunks.replace_membership(
            "main",
            [ChunkMembership("main", 1, "a.py"), ChunkMembership("main", 2, "b.py")],
        )
        await uow.branch_chunks.delete_for_chunk_ids([1])
        rows = await uow.branch_chunks.list_membership("main")
        assert [m.chunk_id for m in rows] == [2]
        await uow.branch_chunks.delete_for_chunk_ids([])  # empty is a no-op
        assert await uow.branch_chunks.count_for_branch("main") == 1
        await uow.commit()


@pytest.mark.parametrize("kind", ["sqlite", "fake"])
async def test_file_extractions_upsert_get_and_unreferenced_delete(kind: str, uow_factory) -> None:
    factory = uow_factory if kind == "sqlite" else make_fake_uow_factory()
    live = FileExtraction("b1", "pkg/a.py", "p", "[[1, 1, 2]]", 5.0)
    stale = FileExtraction("b0", "pkg/a.py", "p", "[[9, 1, 2]]", 4.0)
    async with factory() as uow:
        await uow.file_extractions.upsert_many([live, stale])
        await uow.branches.upsert_branch(_record())
        await uow.branches.replace_files("main", [BranchFile("main", "pkg/a.py", "b1")])
        assert await uow.file_extractions.get("b1", "pkg/a.py", "p") == live
        assert await uow.file_extractions.delete_unreferenced() == 1
        assert await uow.file_extractions.get("b0", "pkg/a.py", "p") is None
        assert await uow.file_extractions.get("b1", "pkg/a.py", "p") == live
        await uow.commit()


@pytest.mark.parametrize("kind", ["sqlite", "fake"])
async def test_every_column_round_trips(kind: str, uow_factory) -> None:
    """The tests above leave every optional column at its default, so a typo in
    one row mapper would pass unnoticed. Pin the fully-populated shape."""
    factory = uow_factory if kind == "sqlite" else make_fake_uow_factory()
    branch = BranchRecord(
        name="feat/x",
        head_sha="b" * 40,
        source=BranchIndexSource.GIT_OBJECTS,
        pipeline_hash="ph",
        indexed_at=1.5,
        last_used_at=2.5,
        is_default=True,
        base_name="main",
        merge_base_sha="c" * 40,
        worktree_path="/tmp/wt",
        status=BranchStatus.MERGED,
        merged_into="main",
        retired_at=3.5,
        purge_after=4.5,
        pinned=True,
    )
    file = BranchFile("feat/x", "a/b.py", "s1", FileChangeKind.RENAMED)
    member = ChunkMembership("feat/x", 7, "a/b.py", 3, 9, True, BranchSlice.DIFF)
    extraction = FileExtraction("s1", "a/b.py", "ph", "[[7, 3, 9]]", 8.5, '{"t":1}', "[2]", "[3]")
    async with factory() as uow:
        await uow.branches.upsert_branch(branch)
        await uow.branches.replace_files("feat/x", [file])
        await uow.branch_chunks.replace_membership("feat/x", [member])
        await uow.file_extractions.upsert_many([extraction])
        await uow.commit()
    async with factory() as uow:
        assert await uow.branches.get_branch("feat/x") == branch
        assert await uow.branches.list_branches() == (branch,)
        assert await uow.branches.list_files("feat/x") == (file,)
        assert await uow.branch_chunks.list_membership("feat/x") == (member,)
        assert await uow.file_extractions.get("s1", "a/b.py", "ph") == extraction


async def test_delete_all_wipes_branch_tables(uow_factory) -> None:
    async with uow_factory() as uow:
        await uow.branches.upsert_branch(_record())
        # ``branch_files`` is seeded too: ``delete_all`` wipes it in its own
        # statement, so without a row here half of the sweep is unpinned.
        await uow.branches.replace_files("main", [BranchFile("main", "pkg/a.py", "b1")])
        await uow.branch_chunks.replace_membership("main", [ChunkMembership("main", 1, "m.py")])
        await uow.file_extractions.upsert_many([FileExtraction("b", "m.py", "p", "[]", 1.0)])
        await uow.delete_all()
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.branches.list_branches() == ()
        assert await uow.branches.count_files("main") == 0
        assert await uow.branches.list_files("main") == ()
        assert await uow.branch_chunks.count_for_branch("main") == 0
        assert await uow.file_extractions.get("b", "m.py", "p") is None
