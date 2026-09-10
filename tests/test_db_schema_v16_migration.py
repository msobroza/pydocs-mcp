"""v16 migration — the branch dimension's tables (spec §6.1, P0).

Mirrors test_db_schema_v15_migration.py: build a v15 db on disk, reopen through
open_index_database, assert the four tables + the chunk hash index exist, that
rows survive, and that ONLY the project package's content_hash was cleared
(forcing one re-extraction that populates the new tables; chunk hashes are
unchanged so nothing re-embeds).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database

_NEW_TABLES = {"branches", "branch_files", "branch_chunks", "file_extractions"}

_V15_SCRIPT = """
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
    PRAGMA user_version = 15;
"""


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _indexes(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def _v15_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_V15_SCRIPT)
    conn.commit()
    conn.close()


def test_schema_version_is_16() -> None:
    assert SCHEMA_VERSION == 16


def test_fresh_db_has_branch_tables_and_hash_index(tmp_path: Path) -> None:
    conn = open_index_database(tmp_path / "fresh.db")
    try:
        assert _tables(conn) >= _NEW_TABLES
        assert "ix_chunks_content_hash" in _indexes(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()


def test_v15_db_upgrades_in_place_and_clears_only_the_project_hash(tmp_path: Path) -> None:
    db = tmp_path / "v15.db"
    _v15_db(db)
    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert _tables(conn) >= _NEW_TABLES
        hashes = dict(conn.execute("SELECT name, content_hash FROM packages"))
        assert hashes == {"__project__": None, "requests": "h2"}
        # chunks and their embedded flags survive: no re-embed is forced.
        # tuple(): open_index_database sets row_factory = sqlite3.Row, and a Row
        # never compares equal to a plain tuple (mirrors the v15 test's tuple(row)).
        row = conn.execute("SELECT content_hash, embedded FROM chunks").fetchone()
        assert tuple(row) == ("c1", 1)
    finally:
        conn.close()


def test_v16_stamped_db_missing_tables_is_repaired_on_open(tmp_path: Path) -> None:
    db = tmp_path / "drift.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        _V15_SCRIPT.replace("PRAGMA user_version = 15;", "PRAGMA user_version = 16;")
    )
    conn.commit()
    conn.close()
    conn = open_index_database(db)
    try:
        assert _tables(conn) >= _NEW_TABLES
        # drift repair never clears content_hash — that is the version step's job
        assert (
            conn.execute("SELECT content_hash FROM packages WHERE name='__project__'").fetchone()[0]
            == "h1"
        )
    finally:
        conn.close()


def test_unknown_version_rebuild_recreates_the_branch_tables(tmp_path: Path) -> None:
    """The four tables must be in ``_KNOWN_TABLES`` so the rebuild drops them.

    An unknown stamp rebuilds from the DDL; a leftover ``branches`` table that
    the drop sweep missed would make ``CREATE TABLE branches`` raise "table
    already exists" — and the rebuild is the fallback for THAT error, so the
    open would fail outright instead of healing.
    """
    db = tmp_path / "unknown.db"
    _v15_db(db)
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE branches (name TEXT PRIMARY KEY);")
    conn.execute("PRAGMA user_version = 999")
    conn.commit()
    conn.close()

    conn = open_index_database(db)
    try:
        assert _tables(conn) >= _NEW_TABLES
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        # a rebuild is a wipe: the old rows are gone, not migrated
        assert conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0] == 0
    finally:
        conn.close()
