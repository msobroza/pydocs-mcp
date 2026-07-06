"""Schema v7 adds chunks.qualified_name TEXT additively (tree-reasoning join key)."""

import sqlite3
from pathlib import Path

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database


def test_schema_version_is_7() -> None:
    assert SCHEMA_VERSION == 13


def test_fresh_db_v7_has_chunks_qualified_name_column(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    open_index_database(db_path).close()
    conn = sqlite3.connect(db_path)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(chunks)")]
    conn.close()
    assert "qualified_name" in cols


def test_v6_to_v7_migration_adds_column_lossless(tmp_path: Path) -> None:
    db_path = tmp_path / "v6.db"
    # Simulate a v6 cache: full v6 shape (chunks WITHOUT qualified_name), stamped
    # at user_version=6. The idempotent v3..v6 sweeps that open_index_database
    # re-runs on a v6-stamped DB require every base table to already exist.
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE packages (
            name TEXT PRIMARY KEY, version TEXT, summary TEXT,
            homepage TEXT, dependencies TEXT, content_hash TEXT,
            origin TEXT, local_path TEXT, embedding_model TEXT
        );
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY, package TEXT,
            module TEXT DEFAULT '',
            title TEXT, text TEXT, origin TEXT,
            content_hash TEXT
        );
        CREATE TABLE module_members (
            id INTEGER PRIMARY KEY, package TEXT, module TEXT,
            name TEXT, kind TEXT, signature TEXT,
            return_annotation TEXT, parameters TEXT, docstring TEXT
        );
        CREATE TABLE document_trees (
            package TEXT NOT NULL, module TEXT NOT NULL, tree_json TEXT NOT NULL,
            content_hash TEXT, updated_at REAL, PRIMARY KEY (package, module)
        );
        CREATE TABLE node_references (
            from_package TEXT NOT NULL, from_node_id TEXT NOT NULL,
            to_name TEXT NOT NULL, to_node_id TEXT, kind TEXT NOT NULL,
            PRIMARY KEY (from_package, from_node_id, to_name, kind)
        );
        CREATE TABLE chunk_multi_vector_ids (
            chunk_id INTEGER PRIMARY KEY, plaid_doc_id INTEGER NOT NULL UNIQUE,
            package TEXT NOT NULL, pipeline_hash TEXT NOT NULL
        );
        PRAGMA user_version = 6;
    """)
    conn.execute(
        "INSERT INTO chunks (package, title, text, origin) VALUES (?, ?, ?, ?)",
        ("__project__", "foo", "def foo(): ...", "project"),
    )
    conn.commit()
    conn.close()

    # Open via the migration path (v6 → v7, additive — no wipe).
    open_index_database(db_path).close()

    conn = sqlite3.connect(db_path)
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(chunks)")]
        assert "qualified_name" in cols
        # Version bumped.
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        # Pre-existing row survived; new column reads NULL on it (not wiped).
        row = conn.execute(
            "SELECT title, qualified_name FROM chunks WHERE package = '__project__'"
        ).fetchone()
        assert row == ("foo", None)
    finally:
        conn.close()
