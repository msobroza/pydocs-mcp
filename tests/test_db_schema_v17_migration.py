"""v17 migration — additive ``index_metadata.loadable_grammars`` (issue #246 item 3).

Mirrors test_db_schema_v13_migration.py (the ``git_head`` column, the same
shape): build a v16 db on disk, reopen through open_index_database, assert the
column landed, rows survived, and — unlike the v16 step — the project's
``content_hash`` is NOT cleared. The stamp is written at the end of EVERY index
pass, extracted or cached, so nothing needs re-extracting to acquire it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database

_V16_SCRIPT = """
    CREATE TABLE packages (name TEXT PRIMARY KEY, version TEXT, summary TEXT,
        homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
        local_path TEXT, embedding_model TEXT);
    CREATE TABLE chunks (id INTEGER PRIMARY KEY, package TEXT,
        module TEXT DEFAULT '', title TEXT, text TEXT, origin TEXT,
        content_hash TEXT, qualified_name TEXT,
        embedded INTEGER NOT NULL DEFAULT 0, decision_id INTEGER,
        source_path TEXT, start_line INTEGER, end_line INTEGER);
    CREATE VIRTUAL TABLE chunks_fts USING fts5(title, text, package,
        content=chunks, content_rowid=id, tokenize='porter unicode61');
    CREATE TABLE module_members (id INTEGER PRIMARY KEY, package TEXT,
        module TEXT, name TEXT, kind TEXT, signature TEXT,
        return_annotation TEXT, parameters TEXT, docstring TEXT);
    CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1),
        project_name TEXT, project_root TEXT, embedding_provider TEXT,
        embedding_model TEXT, embedding_dim INTEGER,
        pipeline_hash TEXT, indexed_at REAL, git_head TEXT,
        activity_summary TEXT, overview_summary TEXT);
    CREATE TABLE decision_records (id INTEGER PRIMARY KEY, package TEXT NOT NULL,
        title TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL,
        confidence REAL NOT NULL, evidence TEXT NOT NULL,
        affected_files TEXT NOT NULL, affected_qnames TEXT NOT NULL,
        staleness_score REAL NOT NULL DEFAULT 0.0, superseded_by INTEGER,
        verification TEXT NOT NULL DEFAULT 'verbatim', structured TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL);
    INSERT INTO packages (name, content_hash, origin) VALUES ('__project__', 'h1', 'project');
    INSERT INTO packages (name, content_hash, origin) VALUES ('requests', 'h2', 'dependency');
    INSERT INTO chunks (package, title, text, content_hash, embedded)
        VALUES ('__project__', 't', 'body', 'c1', 1);
    INSERT INTO index_metadata (id, project_name, pipeline_hash, indexed_at, git_head)
        VALUES (1, 'proj', 'ph', 1.0, 'abc');
    PRAGMA user_version = 16;
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _v16_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_V16_SCRIPT)
    conn.commit()
    conn.close()


def test_loadable_grammars_column_survives_forward_migration() -> None:
    # v17 introduced loadable_grammars; every later version keeps it additively.
    assert SCHEMA_VERSION >= 17


def test_fresh_db_has_loadable_grammars_column(tmp_path: Path) -> None:
    conn = open_index_database(tmp_path / "fresh.db")
    try:
        assert "loadable_grammars" in _columns(conn, "index_metadata")
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()


def test_v16_db_upgrades_in_place_preserving_rows_and_hashes(tmp_path: Path) -> None:
    db = tmp_path / "v16.db"
    _v16_db(db)
    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "loadable_grammars" in _columns(conn, "index_metadata")
        # The existing stamp survives; the new column reads NULL until the next
        # index pass writes it.
        row = conn.execute(
            "SELECT project_name, git_head, loadable_grammars FROM index_metadata"
        ).fetchone()
        assert tuple(row) == ("proj", "abc", None)
        # NO content_hash clear: acquiring the stamp needs an index pass, not a
        # re-extraction — every pass stamps, cached or not.
        hashes = dict(conn.execute("SELECT name, content_hash FROM packages"))
        assert hashes == {"__project__": "h1", "requests": "h2"}
        chunk = conn.execute("SELECT content_hash, embedded FROM chunks").fetchone()
        assert tuple(chunk) == ("c1", 1)
    finally:
        conn.close()


def test_v17_stamped_db_missing_the_column_is_repaired_on_open(tmp_path: Path) -> None:
    db = tmp_path / "drift.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        _V16_SCRIPT.replace("PRAGMA user_version = 16;", "PRAGMA user_version = 17;")
    )
    conn.commit()
    conn.close()
    conn = open_index_database(db)
    try:
        assert "loadable_grammars" in _columns(conn, "index_metadata")
        # drift repair never clears content_hash — that is the version step's job
        assert (
            conn.execute("SELECT content_hash FROM packages WHERE name='__project__'").fetchone()[0]
            == "h1"
        )
    finally:
        conn.close()
