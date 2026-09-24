"""The §6.4 virtual ``branch`` / ``slice`` / ``changed`` filter fields over a real bundle (#312).

Project chunks are shared rows; which branch holds them lives in
``branch_chunks``. A branch pin must therefore turn into ONE correlated
``EXISTS`` over membership on the chunk side (the lexical fetch, the dense
allowlist, repository reads), leave dependency chunks visible on every branch
(the dependency tier every branch reads), and compose with #346's
dependency-decision exclusion. Member rows carry their branch as a column:
the retrieval adapter reads a pinned branch plus the dependency tier, while the
repositories keep the exact match their per-branch deletes rely on (#307).
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.filters import All, FieldEq, FieldIn, Not
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchSlice,
    Chunk,
    ChunkFilterField,
    ChunkOrigin,
)
from pydocs_mcp.retrieval.filter_helpers import (
    with_branch_pin,
    with_dependency_decision_exclusion,
)
from pydocs_mcp.storage.branch_records import ChunkMembership
from pydocs_mcp.storage.factories import (
    build_connection_provider,
    build_sqlite_candidate_id_resolver,
    build_sqlite_uow_factory,
)
from pydocs_mcp.storage.sqlite.filter_adapter import (
    _MEMBER_COLUMNS,
    SqliteFilterAdapter,
    _SqliteFilterTranslator,
)
from pydocs_mcp.retrieval.pipeline.connection import PerCallConnectionProvider
from pydocs_mcp.storage.sqlite.branch_chunk_repository import SqliteBranchChunkRepository
from pydocs_mcp.storage.sqlite.fts_store import SqliteLexicalStore
from pydocs_mcp.storage.sqlite.table_crud import ID_BATCH_SIZE, branch_read_clause

MAIN, FEATURE = "main", "feature/x"


def test_the_chunk_side_pins_branch_and_slice_in_one_correlated_exists() -> None:
    tree = All((FieldEq("package", "x"), FieldEq("branch", "b"), FieldEq("slice", "tree")))
    where, params = SqliteFilterAdapter().adapt(tree, target_field="chunk")
    assert where == (
        "(c.package = ?) AND (c.package != ? OR EXISTS (SELECT 1 FROM branch_chunks bc "
        "WHERE bc.chunk_id = c.id AND bc.branch = ? AND bc.slice = ?))"
    )
    assert params == ("x", PROJECT_PACKAGE_NAME, "b", "tree")


def test_changed_is_a_membership_condition_too() -> None:
    tree = All((FieldEq("branch", "b"), FieldEq("changed", 1)))
    where, params = SqliteFilterAdapter().adapt(tree, target_field="chunk")
    assert where.endswith("AND bc.branch = ? AND bc.changed = ?))")
    assert params == (PROJECT_PACKAGE_NAME, "b", 1)


def test_the_member_side_reads_the_pinned_branch_plus_the_dependency_tier() -> None:
    where, params = SqliteFilterAdapter().adapt(FieldEq("branch", "b"), target_field="member")
    # The tree tier's one read predicate (#307), bound to the pinned name.
    assert (where, params) == (f"({branch_read_clause('branch')})", ("b",))


def test_member_repositories_keep_the_exact_branch_match_their_deletes_need() -> None:
    """#307: the checkout purge deletes through ``{"package": p, "branch": b}`` —
    a dependency-tier match there would delete every dependency's members."""
    translator = _SqliteFilterTranslator(safe_columns=_MEMBER_COLUMNS)
    assert translator.adapt(FieldEq("branch", "b")) == ("branch = ?", ["b"])


def test_a_pin_on_a_table_without_membership_is_still_refused() -> None:
    translator = _SqliteFilterTranslator(safe_columns=frozenset({"name"}))
    with pytest.raises(ValueError, match="'branch' not in safe_columns"):
        translator.adapt(FieldEq("branch", "b"))


# ── A real bundle: two branches, a dependency, decisions on both sides ──


@dataclass(frozen=True, slots=True)
class _Ids:
    shared: int
    main_only: int
    feature_only: int
    project_decision: int
    dependency_code: int
    dependency_decision: int


def _chunk(text: str, package: str = PROJECT_PACKAGE_NAME, **metadata: object) -> Chunk:
    return Chunk(text=text, metadata={ChunkFilterField.PACKAGE.value: package, **metadata})


def _decision(text: str, package: str) -> Chunk:
    return _chunk(text, package, origin=ChunkOrigin.DECISION_RECORD.value)


async def _seed(db: Path) -> _Ids:
    open_index_database(db).close()
    chunks = (
        _chunk("shared helper", source_path="app/a.py", start_line=1, end_line=2),
        _chunk("main only helper", source_path="app/a.py", start_line=4, end_line=5),
        _chunk("feature only helper", source_path="app/b.py", start_line=1, end_line=1),
        _decision("keep rows in memory", PROJECT_PACKAGE_NAME),
        _chunk("requests get helper", "requests"),
        _decision("requests keeps sessions", "requests"),
    )
    async with build_sqlite_uow_factory(db)() as uow:
        ids = _Ids(*await uow.chunks.insert_returning_ids(chunks))
        await uow.branch_chunks.replace_membership(
            MAIN,
            [
                ChunkMembership(MAIN, ids.shared, "app/a.py", 1, 2),
                ChunkMembership(MAIN, ids.main_only, "app/a.py", 4, 5),
                ChunkMembership(MAIN, ids.project_decision, ""),
            ],
        )
        await uow.branch_chunks.replace_membership(
            FEATURE,
            [
                ChunkMembership(FEATURE, ids.shared, "app/a.py", 11, 12),
                ChunkMembership(FEATURE, ids.feature_only, "app/b.py", 1, 1),
                # A diff-slice row naming a chunk only main holds as tree: the
                # tree pin of feature/x must not count it (one EXISTS, not two).
                ChunkMembership(FEATURE, ids.main_only, "app/a.py", 4, 5, slice=BranchSlice.DIFF),
            ],
        )
        await uow.commit()
    return ids


@pytest.fixture
async def seeded(tmp_path: Path) -> tuple[Path, _Ids]:
    db = tmp_path / "pushdown.db"
    return db, await _seed(db)


def _pin(branch: str, tree=None):
    return with_branch_pin(tree, branch, target_field="chunk")


async def _allowlist(db: Path, tree) -> set[int]:
    return set((await build_sqlite_candidate_id_resolver(db)(tree)).tolist())


async def test_the_dense_allowlist_follows_membership_and_keeps_every_dependency(
    seeded: tuple[Path, _Ids],
) -> None:
    db, ids = seeded
    dependencies = {ids.dependency_code, ids.dependency_decision}
    on_main = {ids.shared, ids.main_only, ids.project_decision}
    assert await _allowlist(db, _pin(MAIN)) == on_main | dependencies
    assert await _allowlist(db, _pin(FEATURE)) == {ids.shared, ids.feature_only} | dependencies


async def test_the_dependency_decision_exclusion_still_applies_under_a_pin(
    seeded: tuple[Path, _Ids],
) -> None:
    db, ids = seeded
    excluding = with_dependency_decision_exclusion(None)
    assert await _allowlist(db, _pin(FEATURE, excluding)) == {
        ids.shared,
        ids.feature_only,
        ids.dependency_code,
    }
    assert await _allowlist(db, _pin(MAIN, excluding)) == {
        ids.shared,
        ids.main_only,
        ids.project_decision,
        ids.dependency_code,
    }


async def test_repository_reads_take_the_pin_as_a_plain_filter_mapping(
    seeded: tuple[Path, _Ids],
) -> None:
    db, ids = seeded
    pin = {ChunkFilterField.BRANCH.value: FEATURE, ChunkFilterField.SLICE.value: "tree"}
    async with build_sqlite_uow_factory(db)() as uow:
        listed = await uow.chunks.list(filter=pin)
        counted = await uow.chunks.count(filter=pin)
    expected = {ids.shared, ids.feature_only, ids.dependency_code, ids.dependency_decision}
    assert {c.id for c in listed} == expected and counted == len(expected)


@pytest.mark.parametrize("pinned", [ChunkFilterField.BRANCH.value, ChunkFilterField.SLICE.value])
async def test_a_chunk_delete_refuses_the_membership_fields(
    seeded: tuple[Path, _Ids], pinned: str
) -> None:
    """A read pin keeps every dependency row: as a DELETE it would wipe them all,
    and shared project rows other branches hold — the #307 hazard the member
    side guards against. A chunk delete names real columns only."""
    db, _ids = seeded
    async with build_sqlite_uow_factory(db)() as uow:
        with pytest.raises(ValueError, match=f"{pinned!r} not in safe_columns"):
            await uow.chunks.delete(filter={pinned: "feature/x"})
        assert await uow.chunks.count() == len(_Ids.__dataclass_fields__)


async def test_a_negated_pin_reads_the_rows_the_branch_does_not_hold(
    seeded: tuple[Path, _Ids],
) -> None:
    db, ids = seeded
    project = FieldEq("package", PROJECT_PACKAGE_NAME)
    # Without a slice the pin counts any membership: main_only's diff row on
    # feature/x makes feature/x hold it.
    not_held = Not(FieldEq("branch", FEATURE))
    assert await _allowlist(db, All((project, not_held))) == {ids.project_decision}
    not_in_tree = Not(All((FieldEq("branch", FEATURE), FieldEq("slice", "tree"))))
    assert await _allowlist(db, All((project, not_in_tree))) == {
        ids.main_only,
        ids.project_decision,
    }


async def test_the_lexical_store_honors_the_pin(seeded: tuple[Path, _Ids]) -> None:
    db, ids = seeded
    async with build_sqlite_uow_factory(db)() as uow:
        await uow.chunks.rebuild_index()
        await uow.commit()
    store = SqliteLexicalStore(provider=build_connection_provider(db))
    on_feature = await store.text_search("helper", 10, filter=_pin(FEATURE))
    on_main = await store.text_search("helper", 10, filter=_pin(MAIN))
    assert {c.id for c in on_feature} == {ids.shared, ids.feature_only, ids.dependency_code}
    assert {c.id for c in on_main} == {ids.shared, ids.main_only, ids.dependency_code}


async def test_membership_of_chunks_reads_one_branch_and_one_slice(
    seeded: tuple[Path, _Ids],
) -> None:
    db, ids = seeded
    asked = [ids.shared, ids.main_only, ids.dependency_code]
    async with build_sqlite_uow_factory(db)() as uow:
        tree_rows = await uow.branch_chunks.membership_of_chunks(
            FEATURE, asked, slice=BranchSlice.TREE
        )
        diff_rows = await uow.branch_chunks.membership_of_chunks(
            FEATURE, asked, slice=BranchSlice.DIFF
        )
        none_asked = await uow.branch_chunks.membership_of_chunks(
            FEATURE, [], slice=BranchSlice.TREE
        )
    assert tree_rows == (ChunkMembership(FEATURE, ids.shared, "app/a.py", 11, 12),)
    assert [r.chunk_id for r in diff_rows] == [ids.main_only] and none_asked == ()


@dataclass(frozen=True, slots=True)
class _OneBatchVariableLimitProvider:
    """Opens the bundle with SQLite's bound-variable limit at one id batch
    (plus the branch and slice binds), so an unbatched id list fails to
    prepare instead of passing under the default 32,766-variable limit."""

    inner: PerCallConnectionProvider

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[sqlite3.Connection]:
        async with self.inner.acquire() as conn:
            conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, ID_BATCH_SIZE + 2)
            yield conn


async def test_a_long_id_list_is_read_in_batches(seeded: tuple[Path, _Ids]) -> None:
    db, ids = seeded
    asked = [ids.shared, *range(10_000, 10_000 + 2 * ID_BATCH_SIZE)]
    provider = _OneBatchVariableLimitProvider(PerCallConnectionProvider(cache_path=db))
    store = SqliteBranchChunkRepository(provider=provider)  # type: ignore[arg-type]
    rows = await store.membership_of_chunks(MAIN, asked, slice=BranchSlice.TREE)
    assert [r.chunk_id for r in rows] == [ids.shared]


def test_field_in_is_never_a_membership_condition() -> None:
    """Only equality pins a branch: an IN over branches is not a §6.4 selector."""
    with pytest.raises(ValueError, match="'branch' not in safe_columns"):
        SqliteFilterAdapter().adapt(FieldIn("branch", ("a", "b")), target_field="chunk")
