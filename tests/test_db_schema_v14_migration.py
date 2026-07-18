"""v14 migration — additive decision layer (spec §D8-§D10, §D17).

Mirrors test_db_schema_v13_migration.py: build a previous-version db on disk,
reopen through open_index_database, assert the additive changes landed
(decision_records table, chunks.decision_id backlink, index_metadata JSON
aggregate columns) and no data was lost. The 13→14 branch must NOT re-run the
pre-v12 ``embedded`` backfill (selective-policy flags survive), while a v12 db
walks forward with the backfill intact.
"""

import sqlite3

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_schema_version_is_14() -> None:
    assert SCHEMA_VERSION == 15


def test_fresh_db_has_decision_tables_and_columns(tmp_path) -> None:
    conn = open_index_database(tmp_path / "fresh.db")
    try:
        assert "decision_records" in _tables(conn)
        assert "decision_id" in _columns(conn, "chunks")
        assert {"activity_summary", "overview_summary"} <= _columns(conn, "index_metadata")
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    finally:
        conn.close()


def test_v13_db_upgrades_in_place_preserving_rows(tmp_path) -> None:
    db = tmp_path / "v13.db"
    # Build a minimal v13-shaped db: the v12 builder + git_head column on
    # index_metadata + user_version=13. One chunk with embedded=0 (selective
    # policy) and one stamped index_metadata row (with git_head set).
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE packages (name TEXT PRIMARY KEY, version TEXT, summary TEXT,
            homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
            local_path TEXT, embedding_model TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, package TEXT,
            module TEXT DEFAULT '', title TEXT, text TEXT, origin TEXT,
            content_hash TEXT, qualified_name TEXT,
            embedded INTEGER NOT NULL DEFAULT 0);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(title, text, package,
            content=chunks, content_rowid=id, tokenize='porter unicode61');
        CREATE TABLE module_members (id INTEGER PRIMARY KEY, package TEXT,
            module TEXT, name TEXT, kind TEXT, signature TEXT,
            return_annotation TEXT, parameters TEXT, docstring TEXT);
        CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1),
            project_name TEXT, project_root TEXT, embedding_provider TEXT,
            embedding_model TEXT, embedding_dim INTEGER,
            pipeline_hash TEXT, indexed_at REAL, git_head TEXT);
        INSERT INTO index_metadata VALUES
            (1, 'proj', '/p', 'fastembed', 'bge', 384, 'hash', 1000.0, 'deadbeef');
        INSERT INTO chunks (package, title, text, embedded)
            VALUES ('demo', 't', 'body', 0);
        PRAGMA user_version = 13;
        """
    )
    conn.commit()
    conn.close()

    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
        assert "decision_records" in _tables(conn)
        assert "decision_id" in _columns(conn, "chunks")
        assert {"activity_summary", "overview_summary"} <= _columns(conn, "index_metadata")
        # selective-policy flags must NOT be rewritten on 13→14
        assert conn.execute("SELECT embedded FROM chunks").fetchone()[0] == 0
        assert conn.execute("SELECT git_head FROM index_metadata").fetchone()[0] is not None
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    finally:
        conn.close()


def test_v12_db_walks_forward_with_embedded_backfill(tmp_path) -> None:
    db = tmp_path / "v12.db"
    # A pre-v13 db: chunks with embedded=1 (embed-everything era) + a stamped
    # index_metadata row WITHOUT git_head + user_version=12. The walk-forward
    # branch must add git_head, the v14 decision layer, and keep embedded=1.
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE packages (name TEXT PRIMARY KEY, version TEXT, summary TEXT,
            homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
            local_path TEXT, embedding_model TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, package TEXT,
            module TEXT DEFAULT '', title TEXT, text TEXT, origin TEXT,
            content_hash TEXT, qualified_name TEXT,
            embedded INTEGER NOT NULL DEFAULT 0);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(title, text, package,
            content=chunks, content_rowid=id, tokenize='porter unicode61');
        CREATE TABLE module_members (id INTEGER PRIMARY KEY, package TEXT,
            module TEXT, name TEXT, kind TEXT, signature TEXT,
            return_annotation TEXT, parameters TEXT, docstring TEXT);
        CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1),
            project_name TEXT, project_root TEXT, embedding_provider TEXT,
            embedding_model TEXT, embedding_dim INTEGER,
            pipeline_hash TEXT, indexed_at REAL);
        INSERT INTO index_metadata VALUES
            (1, 'proj', '/p', 'fastembed', 'bge', 384, 'hash', 1000.0);
        INSERT INTO chunks (package, title, text, embedded)
            VALUES ('demo', 't', 'body', 1);
        PRAGMA user_version = 12;
        """
    )
    conn.commit()
    conn.close()

    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
        assert "decision_records" in _tables(conn)
        assert "git_head" in _columns(conn, "index_metadata")
        assert {"activity_summary", "overview_summary"} <= _columns(conn, "index_metadata")
        # v12's embed-everything flags survive the walk forward.
        assert conn.execute("SELECT embedded FROM chunks").fetchone()[0] == 1
    finally:
        conn.close()
