"""Schema v5 adds packages.embedding_model TEXT additively (AC-11)."""

import sqlite3
from pathlib import Path

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database


def test_schema_version_is_5() -> None:
    assert SCHEMA_VERSION == 13


def test_fresh_db_v5_has_packages_embedding_model_column(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    open_index_database(db_path).close()
    conn = sqlite3.connect(db_path)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(packages)")]
    conn.close()
    assert "embedding_model" in cols


def test_v4_to_v5_migration_lossless(tmp_path: Path) -> None:
    db_path = tmp_path / "v4.db"
    # Simulate v4 cache: full v4 shape (packages + chunks + module_members +
    # document_trees + node_references), no embedding_model. The idempotent
    # v3/v4 sweeps that open_index_database re-runs on v4-stamped DBs require
    # every base table to already exist.
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE packages (
            name TEXT PRIMARY KEY, version TEXT, summary TEXT,
            homepage TEXT, dependencies TEXT, content_hash TEXT,
            origin TEXT, local_path TEXT
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
            package TEXT NOT NULL,
            module TEXT NOT NULL,
            tree_json TEXT NOT NULL,
            content_hash TEXT,
            updated_at REAL,
            PRIMARY KEY (package, module)
        );
        CREATE TABLE node_references (
            from_package TEXT NOT NULL, from_node_id TEXT NOT NULL,
            to_name TEXT NOT NULL, to_node_id TEXT, kind TEXT NOT NULL,
            PRIMARY KEY (from_package, from_node_id, to_name, kind)
        );
        PRAGMA user_version = 4;
    """)
    conn.execute(
        "INSERT INTO packages (name, version) VALUES (?, ?)",
        ("demo-pkg", "1.0.0"),
    )
    conn.commit()
    conn.close()
    # Open via the migration path.
    open_index_database(db_path).close()
    conn = sqlite3.connect(db_path)
    # Existing row preserved.
    row = conn.execute(
        "SELECT name, version FROM packages WHERE name = ?",
        ("demo-pkg",),
    ).fetchone()
    assert row == ("demo-pkg", "1.0.0")
    # New column present + defaults to NULL on existing rows.
    embedding_model = conn.execute(
        "SELECT embedding_model FROM packages WHERE name = ?",
        ("demo-pkg",),
    ).fetchone()[0]
    assert embedding_model is None
    # Version bumped (v4 → … → v7 walks all forward migrations in one open).
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    conn.close()
