"""v13 migration — additive ``index_metadata.git_head`` column (spec §D4).

Mirrors test_db_schema_v10_migration.py: build a previous-version db on disk,
reopen through open_index_database, assert the additive change landed and no
data was lost.
"""

import sqlite3

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_schema_version_is_13() -> None:
    assert SCHEMA_VERSION == 13


def test_fresh_db_has_git_head_column(tmp_path) -> None:
    conn = open_index_database(tmp_path / "fresh.db")
    try:
        assert "git_head" in _columns(conn, "index_metadata")
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    finally:
        conn.close()


def test_v12_db_upgrades_in_place_preserving_rows(tmp_path) -> None:
    db = tmp_path / "v12.db"
    # Build a minimal v12-shaped db: chunks with embedded flag + a stamped
    # index_metadata row (no git_head column yet) + user_version=12.
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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        assert "git_head" in _columns(conn, "index_metadata")
        # Row data preserved; new column reads back NULL until next stamp.
        row = conn.execute(
            "SELECT project_name, indexed_at, git_head FROM index_metadata"
        ).fetchone()
        assert (row["project_name"], row["indexed_at"]) == ("proj", 1000.0)
        assert row["git_head"] is None
        # v12's selective-policy embedded flags must NOT be rewritten.
        assert conn.execute("SELECT embedded FROM chunks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    finally:
        conn.close()


def test_v11_db_still_walks_forward_with_embedded_backfill(tmp_path) -> None:
    db = tmp_path / "v11.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE packages (name TEXT PRIMARY KEY, version TEXT, summary TEXT,
            homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
            local_path TEXT, embedding_model TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, package TEXT,
            module TEXT DEFAULT '', title TEXT, text TEXT, origin TEXT,
            content_hash TEXT, qualified_name TEXT);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(title, text, package,
            content=chunks, content_rowid=id, tokenize='porter unicode61');
        CREATE TABLE module_members (id INTEGER PRIMARY KEY, package TEXT,
            module TEXT, name TEXT, kind TEXT, signature TEXT,
            return_annotation TEXT, parameters TEXT, docstring TEXT);
        INSERT INTO chunks (package, title, text) VALUES ('demo', 't', 'body');
        PRAGMA user_version = 11;
        """
    )
    conn.commit()
    conn.close()

    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        assert "git_head" in _columns(conn, "index_metadata")
        # Pre-v12 rows were embed-everything: backfill embedded=1 still runs.
        assert conn.execute("SELECT embedded FROM chunks").fetchone()[0] == 1
    finally:
        conn.close()
