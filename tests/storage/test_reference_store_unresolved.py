"""ReferenceStore.list_unresolved / list_resolved — v14 read-only reads (AC10).

Real SQLite: the additive reads must serve a schema-v14 bundle AS-IS and
leave its ``PRAGMA user_version`` untouched (the G6 read-only proof).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.sqlite import SqliteReferenceStore


def _ref(
    from_node_id: str,
    to_name: str,
    *,
    kind: ReferenceKind = ReferenceKind.CALLS,
    to_node_id: str | None = None,
) -> NodeReference:
    return NodeReference(
        from_package="__project__",
        from_node_id=from_node_id,
        to_name=to_name,
        to_node_id=to_node_id,
        kind=kind,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "bundle.db"
    open_index_database(db).close()
    return db


@pytest.fixture
def store(db_path: Path) -> SqliteReferenceStore:
    return SqliteReferenceStore(provider=PerCallConnectionProvider(cache_path=db_path))


async def _seed(store: SqliteReferenceStore) -> None:
    await store.save_many(
        [
            _ref("a.x", "b.unresolved_call"),
            _ref("a.y", "b.unresolved_import", kind=ReferenceKind.IMPORTS),
            _ref("a.z", "b.mentioned", kind=ReferenceKind.MENTIONS),
            _ref("a.w", "b.resolved", to_node_id="b.resolved"),
            _ref("a.v", "b.res_import", kind=ReferenceKind.IMPORTS, to_node_id="b.res_import"),
        ],
        package="__project__",
    )


async def test_list_unresolved_filters_null_targets_and_kinds(
    store: SqliteReferenceStore,
) -> None:
    await _seed(store)
    rows = await store.list_unresolved((ReferenceKind.CALLS, ReferenceKind.IMPORTS))
    names = sorted(r.to_name for r in rows)
    assert names == ["b.unresolved_call", "b.unresolved_import"]
    assert all(r.to_node_id is None for r in rows)


async def test_list_unresolved_limit(store: SqliteReferenceStore) -> None:
    await _seed(store)
    rows = await store.list_unresolved(
        (ReferenceKind.CALLS, ReferenceKind.IMPORTS, ReferenceKind.MENTIONS), limit=1
    )
    assert len(rows) == 1


async def test_list_resolved_is_kind_aware(store: SqliteReferenceStore) -> None:
    await _seed(store)
    pairs = await store.list_resolved((ReferenceKind.IMPORTS,))
    assert pairs == [("a.v", "b.res_import")]


async def test_reads_leave_the_bundle_version_untouched(
    store: SqliteReferenceStore, db_path: Path
) -> None:
    # AC10 / G6: pure v14 reads — no schema bump, no write.
    await _seed(store)
    await store.list_unresolved((ReferenceKind.CALLS,))
    await store.list_resolved((ReferenceKind.CALLS,))
    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION
