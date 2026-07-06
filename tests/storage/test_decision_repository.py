"""Tests for SqliteDecisionRepository — the UoW's ninth store (spec §D8-§D10).

Real SQLite via ``open_index_database(tmp_path/...)`` for the concrete
repo (mirroring the module-member repo tests), plus a contract-parity
run through :class:`InMemoryDecisionStore` + ``make_fake_uow_factory``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.storage.decision_record import DecisionEvidence, DecisionRecord
from pydocs_mcp.storage.factories import build_connection_provider
from pydocs_mcp.storage.sqlite import SqliteDecisionRepository

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
