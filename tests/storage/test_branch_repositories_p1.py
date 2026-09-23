"""P1 branch stores (#307, plan Task 4): the landing-unit columns and the
patch-id cache, the membership copy by slice, and the per-branch purge.

Every behavior runs on the SQLite stores AND the in-memory fakes the service
tests use, so the fakes are held to the same answers; the atomicity tests run
on SQLite, where the unit of work's transaction is real.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_mcp.application.branch_manifest import BranchManifest
from pydocs_mcp.application.branch_membership import purge_branch_rows, write_branch_membership
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchIndexSource,
    BranchSlice,
    Chunk,
    LandingKind,
    MergeEvidence,
    ModuleMember,
)
from pydocs_mcp.storage.branch_records import (
    BranchFile,
    BranchRecord,
    ChunkMembership,
    FileExtraction,
    LandingPatchId,
)
from pydocs_mcp.storage.decision_record import DecisionRecord
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.node_score import NodeScore
from pydocs_mcp.storage.sqlite.table_crud import ID_BATCH_SIZE
from tests._fakes import SpyVectorStore, make_fake_uow_factory
from tests._schema_v17_bundle import downgrade_bundle_to_v17

PROJECT = PROJECT_PACKAGE_NAME
UNIT = "b" * 40  # a landing unit is keyed by its landing sha


_WORKING_TREE_BRANCH = BranchRecord(
    name="main",
    head_sha="a" * 40,
    source=BranchIndexSource.WORKING_TREE,
    pipeline_hash="p",
    indexed_at=1.0,
    last_used_at=1.0,
    worktree_path="/repo",
)


def _record(name: str, **overrides: object) -> BranchRecord:
    return replace(_WORKING_TREE_BRANCH, name=name, **overrides)


def _unit(name: str = UNIT, landed_at: float | None = 5.0) -> BranchRecord:
    return _record(
        name,
        source=BranchIndexSource.GIT_OBJECTS,
        worktree_path=None,
        landing_kind=LandingKind.MERGE_COMMIT,
        landed_at=landed_at,
        diff_generation_key="k",
        merge_evidence=MergeEvidence.ANCESTOR,
        landing_sha=name,
        upstream_gone=True,
    )


def _manifest(name: str, worktree_path: str | None = "/repo") -> BranchManifest:
    return BranchManifest(
        name=name,
        head_sha="c" * 40,
        source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p",
        files=(),
        worktree_path=worktree_path,
    )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "b.db"
    open_index_database(path).close()
    return path


@pytest.fixture(params=["sqlite", "fake"])
def factory(request: pytest.FixtureRequest, db: Path):
    if request.param == "sqlite":
        return build_sqlite_uow_factory(db)
    return make_fake_uow_factory()


# ── BranchStore: landing-unit columns and the patch-id cache ─────────────


async def test_the_six_landing_columns_round_trip(factory) -> None:
    async with factory() as uow:
        await uow.branches.upsert_branch(_unit())
        await uow.commit()
    async with factory() as uow:
        assert await uow.branches.get_branch(UNIT) == _unit()
        assert await uow.branches.list_branches() == (_unit(),)


async def test_a_pre_v18_bundle_reads_with_the_landing_defaults(db: Path) -> None:
    """The ``branches`` verb lists v16 / v17 bundles without migrating them, so
    a read must not name the v18 columns; a pre-v18 row maps to the defaults
    the v18 migration gives it (fails when the select names every column)."""
    async with build_sqlite_uow_factory(db)() as uow:
        await uow.branches.upsert_branch(_unit())
        await uow.commit()
    downgrade_bundle_to_v17(db)
    async with build_sqlite_uow_factory(db)() as uow:
        stored = await uow.branches.list_branches()
    assert stored == (_record(UNIT, source=BranchIndexSource.GIT_OBJECTS, worktree_path=None),)


async def test_landing_units_list_newest_landing_first_and_skip_branch_rows(factory) -> None:
    async with factory() as uow:
        await uow.branches.upsert_branch(_record("main", is_default=True))
        await uow.branches.upsert_branch(_unit("c" * 40, landed_at=5.0))
        await uow.branches.upsert_branch(_unit("d" * 40, landed_at=None))
        await uow.branches.upsert_branch(_unit("e" * 40, landed_at=9.0))
        # Inserted after "ccc…" with the same landing time: only the name tie-break
        # puts it first.
        await uow.branches.upsert_branch(_unit("a" * 40, landed_at=5.0))
        units = await uow.branches.list_landing_units()
    # A unit with no landing time sorts last, as SQL's NULL does under DESC.
    assert [u.name for u in units] == ["e" * 40, "a" * 40, "c" * 40, "d" * 40]
    assert all(u.is_landing_unit for u in units)


async def test_landing_patch_ids_upsert_read_back_and_update_in_place(factory) -> None:
    async with factory() as uow:
        await uow.branches.upsert_landing_patch_ids(
            [LandingPatchId(UNIT, "pid1"), LandingPatchId("c" * 40, "pid2")]
        )
        await uow.branches.upsert_landing_patch_ids([LandingPatchId("c" * 40, "pid3")])
        await uow.branches.upsert_landing_patch_ids([])  # empty is a no-op
        found = await uow.branches.landing_patch_ids([UNIT, "d" * 40, "c" * 40, UNIT])
        assert await uow.branches.landing_patch_ids([]) == {}
    assert found == {UNIT: "pid1", "c" * 40: "pid3"}


async def test_landing_patch_ids_read_more_shas_than_one_statement_binds(factory) -> None:
    shas = [f"{i:040x}" for i in range(2 * ID_BATCH_SIZE + 1)]
    async with factory() as uow:
        await uow.branches.upsert_landing_patch_ids([LandingPatchId(s, f"p{s}") for s in shas])
        found = await uow.branches.landing_patch_ids(shas)
    assert found == {s: f"p{s}" for s in shas}


async def test_delete_all_clears_the_patch_id_cache_too(factory) -> None:
    async with factory() as uow:
        await uow.branches.upsert_branch(_unit())
        await uow.branches.upsert_landing_patch_ids([LandingPatchId(UNIT, "pid1")])
        await uow.delete_all()
        await uow.commit()
    async with factory() as uow:
        assert await uow.branches.landing_patch_ids([UNIT]) == {}
        assert await uow.branches.list_landing_units() == ()


# ── BranchChunkStore: membership copy and delete, one slice at a time ────


def _membership(branch: str, chunk_id: int, slice: BranchSlice, start: int) -> ChunkMembership:
    return ChunkMembership(branch, chunk_id, "pkg/a.py", start, start + 1, True, slice)


async def test_copy_membership_copies_one_slice_under_the_target_name(factory) -> None:
    async with factory() as uow:
        await uow.branch_chunks.replace_membership(
            "feature/x",
            [
                _membership("feature/x", 1, BranchSlice.TREE, 1),
                _membership("feature/x", 2, BranchSlice.DIFF, 3),
                _membership("feature/x", 3, BranchSlice.DIFF, 5),
            ],
        )
        # The target already holds chunk 2 with another span: the copy replaces it.
        await uow.branch_chunks.replace_membership(
            UNIT, [_membership(UNIT, 2, BranchSlice.DIFF, 9)]
        )
        copied = await uow.branch_chunks.copy_membership("feature/x", UNIT, slice=BranchSlice.DIFF)
        await uow.commit()
    async with factory() as uow:
        assert copied == 2
        assert await uow.branch_chunks.list_membership(UNIT) == (
            _membership(UNIT, 2, BranchSlice.DIFF, 3),
            _membership(UNIT, 3, BranchSlice.DIFF, 5),
        )
        assert await uow.branch_chunks.count_for_branch("feature/x") == 3  # the source is kept


async def test_copy_membership_of_an_empty_slice_copies_nothing(factory) -> None:
    async with factory() as uow:
        await uow.branch_chunks.replace_membership(
            "feature/x", [_membership("feature/x", 1, BranchSlice.TREE, 1)]
        )
        copied = await uow.branch_chunks.copy_membership("feature/x", UNIT, slice=BranchSlice.DIFF)
        assert copied == 0
        assert await uow.branch_chunks.count_for_branch(UNIT) == 0


async def test_delete_for_branch_slice_drops_one_slice_of_one_branch(factory) -> None:
    async with factory() as uow:
        for branch in ("feature/x", "main"):
            await uow.branch_chunks.replace_membership(
                branch,
                [
                    _membership(branch, 1, BranchSlice.TREE, 1),
                    _membership(branch, 2, BranchSlice.DIFF, 3),
                ],
            )
        await uow.branch_chunks.delete_for_branch_slice("feature/x", BranchSlice.DIFF)
        await uow.commit()
    async with factory() as uow:
        assert [m.chunk_id for m in await uow.branch_chunks.list_membership("feature/x")] == [1]
        assert [m.chunk_id for m in await uow.branch_chunks.list_membership("main")] == [1, 2]


# ── purge_branch_rows: every row under one name, then the GC ─────────────


def _tree(module: str) -> DocumentNode:
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module,
        kind=NodeKind.MODULE,
        source_path="pkg/a.py",
        start_line=1,
        end_line=1,
        text=module,
        content_hash=module,
    )


def _member(branch: str) -> ModuleMember:
    return ModuleMember(
        metadata={
            "package": PROJECT,
            "module": "pkg.a",
            "name": "f",
            "kind": "def",
            "branch": branch,
        }
    )


def _decision(title: str, branch: str) -> DecisionRecord:
    return DecisionRecord(
        id=None,
        package=PROJECT,
        title=title,
        status="active",
        source="adr",
        confidence=1.0,
        evidence=(),
        affected_files=(),
        affected_qnames=(),
        staleness_score=0.0,
        superseded_by=None,
        verification="verbatim",
        structured=None,
        created_at=1.0,
        updated_at=1.0,
        branch=branch,
    )


def _project_chunk(text: str) -> Chunk:
    return Chunk.from_test_inputs(package=PROJECT, module="pkg.a", title=text, text=text)


async def _seed_tree_tier(uow, branch: str) -> None:
    await uow.trees.save_many([_tree("pkg.a")], package=PROJECT, branch=branch)
    await uow.module_members.upsert_many([_member(branch)])
    ref = NodeReference(PROJECT, "pkg.a.f", "requests.get", None, ReferenceKind.CALLS)
    await uow.references.save_many([ref], package=PROJECT, branch=branch)
    await uow.node_scores.upsert([NodeScore(PROJECT, "pkg.a.f")], branch=branch)
    await uow.decisions.upsert([_decision(f"decided on {branch}", branch)])


async def _seed_two_branches(uow) -> tuple[int, int, int]:
    """``main`` and ``feature/x`` share chunk ``shared``; ``feature/x`` alone
    holds ``tree_only`` (TREE slice) and ``diff_only`` (DIFF slice)."""
    shared, tree_only, diff_only = await uow.chunks.insert_returning_ids(
        (_project_chunk("shared"), _project_chunk("tree"), _project_chunk("diff"))
    )
    await uow.branches.upsert_branch(_record("main", is_default=True))
    await uow.branches.upsert_branch(_record("feature/x", worktree_path=None))
    await uow.branches.replace_files("main", [BranchFile("main", "pkg/a.py", "blob-a")])
    await uow.branches.replace_files("feature/x", [BranchFile("feature/x", "pkg/b.py", "blob-b")])
    await uow.file_extractions.upsert_many(
        [
            FileExtraction("blob-a", "pkg/a.py", "p", "[]", 1.0),
            FileExtraction("blob-b", "pkg/b.py", "p", "[]", 1.0),
        ]
    )
    await uow.branch_chunks.replace_membership("main", [ChunkMembership("main", shared, "a.py")])
    await uow.branch_chunks.replace_membership(
        "feature/x",
        [
            ChunkMembership("feature/x", shared, "pkg/a.py"),
            ChunkMembership("feature/x", tree_only, "pkg/b.py"),
            ChunkMembership("feature/x", diff_only, "pkg/b.py", slice=BranchSlice.DIFF),
        ],
    )
    for branch in ("main", "feature/x"):
        await _seed_tree_tier(uow, branch)
    return shared, tree_only, diff_only


async def _tree_tier_row_count(uow, branch: str) -> int:
    """Rows ``branch`` reads in the five tables (no dependency-tier rows are seeded)."""
    member_filter = {"package": PROJECT, "branch": branch}
    return sum(
        (
            len(await uow.trees.load_all_in_package(PROJECT, branch=branch)),
            await uow.module_members.count(filter=member_filter),
            len(await uow.references.find_callees(from_node_id="pkg.a.f", branch=branch)),
            len(await uow.node_scores.for_package(PROJECT, branch=branch)),
            len(await uow.decisions.list_for_package(PROJECT, branch=branch)),
        )
    )


async def test_purge_branch_rows_drops_every_row_under_the_name_and_keeps_siblings(
    factory,
) -> None:
    async with factory() as uow:
        shared, tree_only, diff_only = await _seed_two_branches(uow)
        await uow.commit()
    async with factory() as uow:
        freed = await purge_branch_rows(uow, "feature/x")
        await uow.commit()
    assert sorted(freed) == sorted((tree_only, diff_only))
    async with factory() as uow:
        assert await uow.branch_chunks.count_for_branch("feature/x") == 0  # both slices
        assert await uow.branches.count_files("feature/x") == 0
        assert await _tree_tier_row_count(uow, "feature/x") == 0
        assert await _tree_tier_row_count(uow, "main") == 5
        assert await uow.branch_chunks.count_for_branch("main") == 1
        assert [c.id for c in await uow.chunks.list(filter={"package": PROJECT})] == [shared]
        assert await uow.file_extractions.get("blob-b", "pkg/b.py", "p") is None
        assert await uow.file_extractions.get("blob-a", "pkg/a.py", "p") is not None
        # The record stays as the tombstone (spec §6.8a).
        assert await uow.branches.get_branch("feature/x") == _record(
            "feature/x", worktree_path=None
        )


async def test_purge_branch_rows_drops_the_freed_chunks_vectors() -> None:
    vectors = SpyVectorStore()
    factory = make_fake_uow_factory(vectors=vectors)
    async with factory() as uow:
        _, tree_only, diff_only = await _seed_two_branches(uow)
        freed = await purge_branch_rows(uow, "feature/x")
        await uow.commit()
    assert sorted(vectors.removed) == sorted(freed) == sorted((tree_only, diff_only))


# ── Atomicity through the unit of work ───────────────────────────────────


class _AbortedTransactionError(Exception):
    pass


async def test_copy_replace_and_purge_roll_back_together(db: Path) -> None:
    factory = build_sqlite_uow_factory(db)
    async with factory() as uow:
        await _seed_two_branches(uow)
        await uow.commit()
    with pytest.raises(_AbortedTransactionError):
        async with factory() as uow:
            await uow.branch_chunks.copy_membership("feature/x", UNIT, slice=BranchSlice.DIFF)
            await uow.branch_chunks.delete_for_branch_slice("main", BranchSlice.TREE)
            await uow.branch_chunks.replace_membership("main", [])
            await uow.branches.replace_files("main", [])
            await uow.branches.upsert_landing_patch_ids([LandingPatchId(UNIT, "pid")])
            await purge_branch_rows(uow, "feature/x")
            raise _AbortedTransactionError
    async with factory() as uow:
        assert await uow.branch_chunks.count_for_branch(UNIT) == 0
        assert await uow.branch_chunks.count_for_branch("main") == 1
        assert await uow.branch_chunks.count_for_branch("feature/x") == 3
        assert await uow.branches.count_files("main") == 1
        assert await uow.branches.count_files("feature/x") == 1
        assert await uow.branches.landing_patch_ids([UNIT]) == {}
        assert await _tree_tier_row_count(uow, "feature/x") == 5
        assert len(await uow.chunks.list(filter={"package": PROJECT})) == 3


async def test_a_committed_copy_and_purge_land_together(db: Path) -> None:
    factory = build_sqlite_uow_factory(db)
    async with factory() as uow:
        await _seed_two_branches(uow)
        await uow.commit()
    async with factory() as uow:
        await uow.branch_chunks.copy_membership("feature/x", UNIT, slice=BranchSlice.DIFF)
        await purge_branch_rows(uow, "feature/x")
        await uow.commit()
    async with factory() as uow:
        # The unit's copy keeps the diff chunk alive through the purge's GC.
        assert [m.slice for m in await uow.branch_chunks.list_membership(UNIT)] == [
            BranchSlice.DIFF
        ]
        assert len(await uow.chunks.list(filter={"package": PROJECT})) == 2


# ── The working-tree stamp never retires a landing unit (spec §6.5b) ─────


async def _write_stamp(factory, manifest: BranchManifest) -> list[str]:
    async with factory() as uow:
        await write_branch_membership(uow, manifest=manifest, assignments=(), now=2.0)
        await uow.commit()
        return [b.name for b in await uow.branches.list_branches()]


async def test_the_stamp_retires_only_the_previous_branch_of_its_own_worktree(factory) -> None:
    async with factory() as uow:
        await uow.branches.upsert_branch(_record("old"))
        await uow.branches.upsert_branch(_record("elsewhere", worktree_path="/other"))
        await uow.branches.upsert_branch(_record("feature/y", worktree_path=None))
        await uow.branches.upsert_branch(_unit())
        await uow.branch_chunks.replace_membership(UNIT, [ChunkMembership(UNIT, 1, "a.py")])
        await uow.commit()
    names = await _write_stamp(factory, _manifest("main"))
    assert sorted(names) == sorted([UNIT, "elsewhere", "feature/y", "main"])
    async with factory() as uow:
        assert await uow.branch_chunks.count_for_branch(UNIT) == 1


async def test_a_stamp_without_a_worktree_retires_no_row_that_has_none(factory) -> None:
    async with factory() as uow:
        await uow.branches.upsert_branch(_record("feature/y", worktree_path=None))
        await uow.branches.upsert_branch(_unit())
        await uow.commit()
    names = await _write_stamp(factory, _manifest("main", worktree_path=None))
    assert sorted(names) == sorted([UNIT, "feature/y", "main"])


async def test_a_landing_unit_naming_the_worktree_is_still_never_retired(factory) -> None:
    """The column decides a landing unit (``BranchRecord.is_landing_unit``),
    not the worktree path it happens to carry."""
    async with factory() as uow:
        await uow.branches.upsert_branch(_record(UNIT, landing_kind=LandingKind.SINGLE_COMMIT))
        await uow.branch_chunks.replace_membership(UNIT, [ChunkMembership(UNIT, 1, "a.py")])
        await uow.commit()
    names = await _write_stamp(factory, _manifest("main"))
    assert sorted(names) == sorted([UNIT, "main"])
    async with factory() as uow:
        assert await uow.branch_chunks.count_for_branch(UNIT) == 1
