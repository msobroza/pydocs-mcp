"""Drifted version-stamped DBs must open — never crash-loop.

The version-specific migration branches (v12→v14, v13→v14) run
``_try_add_column`` sweeps that only swallow "duplicate column" errors. On a
drifted DB (stamped vN but missing a table the sweep ALTERs — e.g.
``index_metadata``), the branch raised ``sqlite3.OperationalError: no such
table`` BEFORE stamping ``user_version``, so every subsequent open took the
same branch and crashed again: a permanent, un-self-healing crash-loop that
bricked the cache until the user manually deleted the ``.db``.

Contract pinned here:
- structural drift that the idempotent sweeps can heal (missing
  ``index_metadata`` / ``node_scores`` / ``document_trees``) is repaired IN
  PLACE, preserving data in the surviving tables;
- unhealable drift (a missing CORE table like ``chunks``) falls back to a
  full rebuild — the cache is derived data, so an empty, working DB beats a
  bricked one;
- either way, the reopened DB is stamped current and opens cleanly forever
  after.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _build_drifted_db(
    db: Path, *, stamp: int, with_index_metadata: bool, with_chunks: bool
) -> None:
    """A vN-stamped DB with deliberate structural drift.

    Mirrors the minimal legacy-shape builders in
    tests/test_db_schema_v14_migration.py, minus the tables under test.
    """
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE packages (name TEXT PRIMARY KEY, version TEXT, summary TEXT,
            homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
            local_path TEXT, embedding_model TEXT);
        CREATE TABLE module_members (id INTEGER PRIMARY KEY, package TEXT,
            module TEXT, name TEXT, kind TEXT, signature TEXT,
            return_annotation TEXT, parameters TEXT, docstring TEXT);
        """
    )
    if with_chunks:
        conn.executescript(
            """
            CREATE TABLE chunks (id INTEGER PRIMARY KEY, package TEXT,
                module TEXT DEFAULT '', title TEXT, text TEXT, origin TEXT,
                content_hash TEXT, qualified_name TEXT,
                embedded INTEGER NOT NULL DEFAULT 0);
            CREATE VIRTUAL TABLE chunks_fts USING fts5(title, text, package,
                content=chunks, content_rowid=id, tokenize='porter unicode61');
            INSERT INTO chunks (package, title, text, embedded)
                VALUES ('demo', 't', 'body', 0);
            """
        )
    if with_index_metadata:
        conn.executescript(
            """
            CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1),
                project_name TEXT, project_root TEXT, embedding_provider TEXT,
                embedding_model TEXT, embedding_dim INTEGER,
                pipeline_hash TEXT, indexed_at REAL, git_head TEXT);
            """
        )
    conn.execute("INSERT INTO packages (name) VALUES ('demo')")
    conn.execute(f"PRAGMA user_version = {stamp}")
    conn.commit()
    conn.close()


@pytest.mark.parametrize("stamp", [12, 13])
def test_missing_index_metadata_heals_in_place(tmp_path: Path, stamp: int) -> None:
    """The exact reproduced crash-loop: a v12/v13-stamped DB without
    ``index_metadata`` must open, be healed by the idempotent sweeps, and
    keep every surviving row."""
    db = tmp_path / f"drifted_v{stamp}.db"
    _build_drifted_db(db, stamp=stamp, with_index_metadata=False, with_chunks=True)

    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "index_metadata" in _tables(conn)
        assert "decision_records" in _tables(conn)
        # In-place heal: the data in surviving tables is preserved.
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
        assert conn.execute("SELECT name FROM packages").fetchone()[0] == "demo"
    finally:
        conn.close()


@pytest.mark.parametrize("stamp", [12, 13])
def test_missing_core_table_rebuilds_instead_of_bricking(tmp_path: Path, stamp: int) -> None:
    """Unhealable drift (no ``chunks`` table at all): the sweeps cannot ALTER
    a missing core table, so the open must fall back to a full rebuild —
    an empty working cache, not a permanent OperationalError."""
    db = tmp_path / f"broken_v{stamp}.db"
    _build_drifted_db(db, stamp=stamp, with_index_metadata=True, with_chunks=False)

    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert {"chunks", "packages", "index_metadata", "decision_records"} <= _tables(conn)
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    finally:
        conn.close()


def test_drifted_db_opens_cleanly_on_every_subsequent_open(tmp_path: Path) -> None:
    """The loop half of the crash-loop: after the first healing open, later
    opens take the stamped-current path and must also succeed."""
    db = tmp_path / "drifted_loop.db"
    _build_drifted_db(db, stamp=13, with_index_metadata=False, with_chunks=True)

    open_index_database(db).close()
    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    finally:
        conn.close()
