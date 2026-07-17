"""v15 migration — additive chunk source spans (tool-contracts §3 items[]).

Mirrors test_db_schema_v14_migration.py: build a previous-version db on disk,
reopen through open_index_database, assert the additive changes landed
(chunks.source_path / start_line / end_line) and no data was lost. The 14→15
branch must NOT re-run the pre-v12 ``embedded`` backfill (selective-policy
flags survive), and a v15-stamped db missing the columns is healed by the
on-open drift-repair sweep.
"""

import sqlite3

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database

_SPAN_COLUMNS = {"source_path", "start_line", "end_line"}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


# Minimal v14-shaped db: the v13 builder + the decision layer (decision_records,
# chunks.decision_id, index_metadata JSON aggregates) + user_version=14.
_V14_SCRIPT = """
    CREATE TABLE packages (name TEXT PRIMARY KEY, version TEXT, summary TEXT,
        homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
        local_path TEXT, embedding_model TEXT);
    CREATE TABLE chunks (id INTEGER PRIMARY KEY, package TEXT,
        module TEXT DEFAULT '', title TEXT, text TEXT, origin TEXT,
        content_hash TEXT, qualified_name TEXT,
        embedded INTEGER NOT NULL DEFAULT 0, decision_id INTEGER);
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
    INSERT INTO index_metadata (id, project_name, project_root, git_head)
        VALUES (1, 'proj', '/p', 'deadbeef');
    INSERT INTO chunks (package, title, text, embedded)
        VALUES ('demo', 't', 'body', 0);
    PRAGMA user_version = 14;
"""


def test_schema_version_is_15() -> None:
    assert SCHEMA_VERSION == 15


def test_fresh_db_has_span_columns(tmp_path) -> None:
    conn = open_index_database(tmp_path / "fresh.db")
    try:
        assert _columns(conn, "chunks") >= _SPAN_COLUMNS
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    finally:
        conn.close()


def test_v14_db_upgrades_in_place_preserving_rows(tmp_path) -> None:
    db = tmp_path / "v14.db"
    conn = sqlite3.connect(db)
    conn.executescript(_V14_SCRIPT)
    conn.commit()
    conn.close()

    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
        assert _columns(conn, "chunks") >= _SPAN_COLUMNS
        # selective-policy flags must NOT be rewritten on 14→15
        assert conn.execute("SELECT embedded FROM chunks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
        # legacy rows read back NULL spans until the next index re-extracts
        row = conn.execute("SELECT source_path, start_line, end_line FROM chunks").fetchone()
        assert tuple(row) == (None, None, None)
    finally:
        conn.close()


def test_v15_stamped_db_missing_columns_is_repaired_on_open(tmp_path) -> None:
    """Drift repair: a v15-stamped db lacking the span columns gets them added."""
    db = tmp_path / "drift.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        _V14_SCRIPT.replace("PRAGMA user_version = 14;", "PRAGMA user_version = 15;")
    )
    conn.commit()
    conn.close()

    conn = open_index_database(db)
    try:
        assert _columns(conn, "chunks") >= _SPAN_COLUMNS
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    finally:
        conn.close()
