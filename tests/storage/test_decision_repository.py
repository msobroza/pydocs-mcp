"""Tests for SqliteDecisionRepository — the UoW's ninth store (spec §D8-§D10).

Real SQLite via ``open_index_database(tmp_path/...)`` for the concrete
repo (mirroring the module-member repo tests), plus a contract-parity
run through :class:`InMemoryDecisionStore` + ``make_fake_uow_factory``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, replace

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import BranchIndexSource
from pydocs_mcp.retrieval.pipeline.connection import PerCallConnectionProvider
from pydocs_mcp.storage.branch_records import BranchRecord
from pydocs_mcp.storage.decision_record import DecisionEvidence, DecisionRecord
from pydocs_mcp.storage.factories import build_connection_provider
from pydocs_mcp.storage.sqlite import SqliteBranchRepository, SqliteDecisionRepository
from pydocs_mcp.storage.sqlite.table_crud import ID_BATCH_SIZE

from tests._fakes import InMemoryDecisionStore, make_fake_uow_factory


@pytest.fixture
def store(tmp_path):
    f = tmp_path / "decisions.db"
    open_index_database(f).close()
    provider = build_connection_provider(f)
    return SqliteDecisionRepository(provider=provider)


def _record(title="Use SQLite sidecar", **kw) -> DecisionRecord:
    defaults = dict(
        package="__project__",
        title=title,
        status="active",
        source="inline_markers",
        confidence=0.95,
        evidence=(
            DecisionEvidence(
                source="inline_markers",
                locator="pkg/mod.py:10-30",
                text="# DECISION: sidecar file for vectors",
            ),
        ),
        affected_files=("pkg/mod.py",),
        affected_qnames=("pkg.mod",),
        staleness_score=0.0,
        superseded_by=None,
        verification="verbatim",
        structured=None,
        created_at=100.0,
        updated_at=100.0,
    )
    defaults.update(kw)
    return DecisionRecord(id=None, **defaults)


async def test_upsert_assigns_id_and_round_trips(store) -> None:
    ids = await store.upsert((_record(),))
    rows = await store.list_for_package("__project__")
    assert rows[0].id == ids[0] and rows[0].title == "Use SQLite sidecar"
    assert rows[0].evidence[0].locator == "pkg/mod.py:10-30"


async def test_update_by_id_preserves_created_at(store) -> None:
    (rid,) = await store.upsert((_record(),))
    updated = replace(
        (await store.list_for_package("__project__"))[0],
        status="superseded",
        updated_at=200.0,
    )
    await store.upsert((updated,))
    rows = await store.list_for_package("__project__")
    assert len(rows) == 1 and rows[0].status == "superseded" and rows[0].created_at == 100.0


async def test_delete_for_package(store) -> None:
    await store.upsert((_record(package="__project__"), _record(package="requests")))
    await store.delete_for_package("__project__")
    assert await store.list_for_package("__project__") == ()
    assert len(await store.list_for_package("requests")) == 1


async def test_delete_by_ids(store) -> None:
    ids = await store.upsert(
        (
            _record(title="keep"),
            _record(title="drop"),
        )
    )
    await store.delete_by_ids((ids[1],))
    rows = await store.list_for_package("__project__")
    assert [r.title for r in rows] == ["keep"]
    # Empty ids is a no-op (no statement executed).
    await store.delete_by_ids(())
    assert len(await store.list_for_package("__project__")) == 1


async def test_delete_all(store) -> None:
    await store.upsert((_record(package="__project__"), _record(package="requests")))
    await store.delete_all()
    assert await store.list_for_package("__project__") == ()
    assert await store.list_for_package("requests") == ()


async def test_fake_store_mirrors_contract() -> None:
    decisions = InMemoryDecisionStore()
    factory = make_fake_uow_factory(decisions=decisions)

    async with factory() as uow:
        ids = await uow.decisions.upsert((_record(),))
        await uow.commit()
    async with factory() as uow:
        rows = await uow.decisions.list_for_package("__project__")
    assert rows[0].id == ids[0] and rows[0].title == "Use SQLite sidecar"
    assert rows[0].evidence[0].locator == "pkg/mod.py:10-30"

    async with factory() as uow:
        updated = replace(rows[0], status="superseded", updated_at=200.0)
        await uow.decisions.upsert((updated,))
        await uow.commit()
    async with factory() as uow:
        rows = await uow.decisions.list_for_package("__project__")
    assert len(rows) == 1 and rows[0].status == "superseded" and rows[0].created_at == 100.0

    async with factory() as uow:
        await uow.decisions.delete_for_package("__project__")
    async with factory() as uow:
        assert await uow.decisions.list_for_package("__project__") == ()


# ── Cross-package reads: decision search hydration + the deps corpus (#346) ──


@pytest.fixture(params=["sqlite", "in_memory"])
def either_store(request, tmp_path):
    """The SQLite repository and the fake the service tests run against."""
    if request.param == "in_memory":
        return InMemoryDecisionStore()
    f = tmp_path / "decisions.db"
    open_index_database(f).close()
    return SqliteDecisionRepository(provider=build_connection_provider(f))


async def _seed_three_packages(store) -> tuple[int, ...]:
    """A project record on ``main`` and two dependency records in the ``''`` tier."""
    return await store.upsert(
        (
            _record(title="project", package="__project__", branch="main"),
            _record(title="requests", package="requests"),
            _record(title="attrs", package="attrs"),
        )
    )


async def test_list_packages_names_each_package_with_records_once(either_store) -> None:
    await _seed_three_packages(either_store)
    await either_store.upsert((_record(title="second", package="requests"),))
    listed = await either_store.list_packages(branch="main")
    assert listed == ("__project__", "attrs", "requests")


async def test_list_packages_follows_the_branch_read_rule(either_store) -> None:
    await _seed_three_packages(either_store)
    await either_store.upsert((_record(title="elsewhere", package="gone", branch="feature/x"),))
    # '' reads the dependency tier only; a name reads that branch plus ''.
    assert await either_store.list_packages(branch="") == ("attrs", "requests")
    assert await either_store.list_packages(branch="feature/x") == ("attrs", "gone", "requests")


async def test_list_by_ids_hydrates_across_packages_in_id_order(either_store) -> None:
    project_id, requests_id, attrs_id = await _seed_three_packages(either_store)
    rows = await either_store.list_by_ids((attrs_id, 999, project_id), branch="main")
    assert [(r.id, r.package) for r in rows] == [
        (project_id, "__project__"),
        (attrs_id, "attrs"),
    ]
    assert await either_store.list_by_ids((), branch="main") == ()


async def test_list_by_ids_follows_the_branch_read_rule(either_store) -> None:
    ids = await _seed_three_packages(either_store)
    on_dependency_tier = await either_store.list_by_ids(ids, branch="")
    assert [r.package for r in on_dependency_tier] == ["requests", "attrs"]


@dataclass(frozen=True, slots=True)
class VariableLimitedConnectionProvider:
    """A real provider whose connections bind at most ``variable_limit`` ``?``s.

    This SQLite build allows 32766 bound variables, so a 1199-id ``IN (...)``
    succeeds unbatched; lowering the per-connection limit to one batch plus the
    branch parameter is what makes a missing batch loop raise.
    """

    inner: PerCallConnectionProvider
    variable_limit: int

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[sqlite3.Connection]:
        async with self.inner.acquire() as conn:
            conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, self.variable_limit)
            yield conn

    @contextmanager
    def acquire_sync(self) -> Iterator[sqlite3.Connection]:
        with self.inner.acquire_sync() as conn:
            conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, self.variable_limit)
            yield conn


async def test_list_by_ids_batches_under_the_sqlite_variable_limit(tmp_path) -> None:
    f = tmp_path / "limited.db"
    open_index_database(f).close()
    provider = build_connection_provider(f)
    ids = await _seed_three_packages(SqliteDecisionRepository(provider=provider))
    # One batch of ids plus the branch parameter: the most one batched statement binds.
    limited = VariableLimitedConnectionProvider(provider, variable_limit=ID_BATCH_SIZE + 1)
    wanted = tuple(range(1, 3 * ID_BATCH_SIZE))
    rows = await SqliteDecisionRepository(provider=limited).list_by_ids(wanted, branch="main")
    assert tuple(r.id for r in rows) == tuple(sorted(ids))


async def test_the_default_read_serves_the_default_branch(tmp_path) -> None:
    """``branch=None`` resolves the served default branch, as every reader does."""
    f = tmp_path / "served.db"
    open_index_database(f).close()
    provider = build_connection_provider(f)
    store = SqliteDecisionRepository(provider=provider)
    ids = await _seed_three_packages(store)
    assert await store.list_packages() == ("attrs", "requests")
    served = BranchRecord(
        name="main",
        head_sha="a" * 40,
        source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p",
        indexed_at=1.0,
        last_used_at=1.0,
        is_default=True,
    )
    await SqliteBranchRepository(provider=provider).upsert_branch(served)
    assert await store.list_packages() == ("__project__", "attrs", "requests")
    assert len(await store.list_by_ids(ids)) == 3
