"""Two processes opening one bundle while it migrates — no row may be lost.

The v17 → v18 step copies three tables and holds SQLite's write lock for
seconds on a large bundle, longer than a connection's ordinary busy wait. A
second opener (two MCP sessions starting servers on one project, ``index`` run
while ``serve`` starts) that read the old stamp used to hit SQLITE_BUSY mid-
sweep, take it for schema drift and rebuild the bundle from scratch — wiping
the rows the first process had just migrated. The open now takes the write
lock before it migrates and reads the stamp again under it, and lock
contention, a full disk or an I/O failure is raised, never "repaired" by a
rebuild. Each test shortens the ordinary wait so a regression fails in well
under a second instead of after sqlite3's 5 s default.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from pydocs_mcp import db as db_module
from pydocs_mcp import db_migration_lock
from pydocs_mcp.db import SCHEMA_VERSION, open_index_database
from tests._schema_v17_bundle import create_v17_bundle, table_rows

_SHORT_ORDINARY_WAIT_SECONDS = 0.05
# Long enough that a second opener has read the old stamp and is waiting on the
# lock before the holder migrates; many times the shortened ordinary wait, so
# an opener that did NOT wait for the lock fails.
_HOLD_SECONDS = 0.3

_SEED_SQL = """
    INSERT INTO packages (name, content_hash, origin) VALUES ('__project__', 'h1', 'project');
    INSERT INTO packages (name, content_hash, origin) VALUES ('requests', 'h2', 'dependency');
    INSERT INTO chunks (package, title, text, content_hash, embedded)
        VALUES ('__project__', 't', 'body', 'c1', 1);
    INSERT INTO document_trees (package, module, tree_json, content_hash, updated_at)
        VALUES ('requests', 'requests.api', '{"d":1}', 'td', 1.0);
    INSERT INTO document_trees (package, module, tree_json, content_hash, updated_at)
        VALUES ('__project__', 'pkg.a', '{"p":1}', 'ta', 2.0);
    INSERT INTO node_references (from_package, from_node_id, to_name, to_node_id, kind)
        VALUES ('__project__', 'pkg.a.f', 'g', 'pkg.b.g', 'calls');
    INSERT INTO node_scores (package, qualified_name, in_degree, pagerank, community)
        VALUES ('__project__', 'pkg.a.f', 2, 0.5, 1);
    INSERT INTO branches (name, head_sha, source, is_default, pipeline_hash, indexed_at,
        last_used_at) VALUES ('main', 'aaaa', 'working_tree', 1, 'p', 1.0, 1.0);
"""
_ROWS_THAT_MUST_SURVIVE = {
    "packages": "name, content_hash",
    "chunks": "package, content_hash, embedded",
    "document_trees": "package, module, tree_json",
    "node_references": "from_package, from_node_id, to_name",
    "node_scores": "package, qualified_name, pagerank",
}


@pytest.fixture
def short_ordinary_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_module, "_CONNECTION_BUSY_WAIT_SECONDS", _SHORT_ORDINARY_WAIT_SECONDS)


def _v17_wal_bundle(path: Path) -> Path:
    """A populated v17 bundle in WAL mode — what every 0.8.x open left on disk."""
    create_v17_bundle(path, _SEED_SQL)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    return path


def _surviving_rows(path: Path) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(path)) as conn:
        return {t: table_rows(conn, t, cols) for t, cols in _ROWS_THAT_MUST_SURVIVE.items()}


def _user_version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _hold_write_lock(path: Path) -> sqlite3.Connection:
    """Another process's connection, holding the bundle's write lock."""
    holder = sqlite3.connect(path, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    return holder


class _OpenInThread:
    """``open_index_database`` on a worker thread, keeping its outcome."""

    def __init__(self, path: Path) -> None:
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._open, args=(path,), daemon=True)
        self._thread.start()

    def _open(self, path: Path) -> None:
        try:
            open_index_database(path).close()
        except BaseException as exc:  # handed to the test thread by join()
            self.error = exc

    def join(self) -> None:
        self._thread.join(timeout=30)
        assert not self._thread.is_alive(), "the second opener never returned"
        if self.error is not None:
            raise self.error


@pytest.mark.usefixtures("short_ordinary_wait")
def test_a_second_opener_waits_for_the_migrating_one_and_keeps_every_row(tmp_path: Path) -> None:
    db = _v17_wal_bundle(tmp_path / "shared.db")
    before = _surviving_rows(db)
    holder = _hold_write_lock(db)
    try:
        second = _OpenInThread(db)
        time.sleep(_HOLD_SECONDS)
        # The first process migrates under the lock it holds, then commits.
        db_module._migrate_in_place(holder, 17)
        holder.commit()
    finally:
        holder.close()
    second.join()
    assert _user_version(db) == SCHEMA_VERSION
    assert _surviving_rows(db) == before
    with closing(sqlite3.connect(db)) as conn:
        stamped = conn.execute("SELECT package, branch FROM document_trees ORDER BY rowid")
        assert stamped.fetchall() == [("requests", ""), ("__project__", "main")]


@pytest.mark.usefixtures("short_ordinary_wait")
def test_an_opener_that_outwaits_the_lock_fails_loudly_and_leaves_the_bundle_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db_migration_lock, "MIGRATION_LOCK_WAIT_MS", 100)
    db = _v17_wal_bundle(tmp_path / "shared.db")
    before = _surviving_rows(db)
    holder = _hold_write_lock(db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked") as raised:
            open_index_database(db)
    finally:
        holder.rollback()
        holder.close()
    assert any(str(db) in note for note in raised.value.__notes__)
    assert _user_version(db) == 17
    assert _surviving_rows(db) == before


def test_a_full_disk_mid_migration_is_raised_not_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQLITE_FULL is the environment, not schema drift: rebuilding cannot heal
    it, and the rebuild's DROPs would destroy a bundle that is intact."""
    db = _v17_wal_bundle(tmp_path / "full.db")
    before = _surviving_rows(db)
    real_rebuild = db_module.rebuild_tree_tier_with_branch_key

    def _rebuild_on_a_full_disk(conn: sqlite3.Connection) -> None:
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        conn.execute(f"PRAGMA max_page_count = {pages}")
        real_rebuild(conn)

    monkeypatch.setattr(db_module, "rebuild_tree_tier_with_branch_key", _rebuild_on_a_full_disk)
    with pytest.raises(sqlite3.OperationalError, match="full"):
        open_index_database(db)
    assert _user_version(db) == 17
    assert _surviving_rows(db) == before


@pytest.mark.usefixtures("short_ordinary_wait")
def test_a_locked_rollback_journal_bundle_is_not_taken_for_a_corrupt_file(tmp_path: Path) -> None:
    """Switching a rollback-journal file to WAL needs an exclusive lock; while
    another connection writes, that PRAGMA raises SQLITE_BUSY — a DatabaseError
    like a corrupt file's, which used to get the bundle DELETED."""
    db = tmp_path / "journal.db"
    create_v17_bundle(db, _SEED_SQL)  # rollback-journal mode, as created
    before = _surviving_rows(db)
    holder = _hold_write_lock(db)
    try:
        holder.execute("UPDATE packages SET version = '1' WHERE name = 'requests'")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            open_index_database(db)
    finally:
        holder.rollback()
        holder.close()
    assert _surviving_rows(db) == before


@pytest.mark.usefixtures("short_ordinary_wait")
def test_an_up_to_date_bundle_opens_without_waiting_for_the_write_lock(tmp_path: Path) -> None:
    """Only a due migration takes the lock: a server starting while an index
    pass holds a write transaction must not wait for it."""
    db = tmp_path / "current.db"
    open_index_database(db).close()
    holder = _hold_write_lock(db)
    try:
        holder.execute("INSERT INTO packages (name) VALUES ('writing')")
        open_index_database(db).close()
    finally:
        holder.rollback()
        holder.close()
    assert _user_version(db) == SCHEMA_VERSION
