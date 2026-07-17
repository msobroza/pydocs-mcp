"""Schema v6: chunk_multi_vector_ids — id-mapping table for fast-plaid."""

from __future__ import annotations

import sqlite3

from pydocs_mcp.db import SCHEMA_VERSION, _KNOWN_TABLES, open_index_database


def test_schema_version_is_6() -> None:
    assert SCHEMA_VERSION == 15


def test_known_tables_includes_chunk_multi_vector_ids() -> None:
    assert "chunk_multi_vector_ids" in _KNOWN_TABLES


def test_fresh_db_has_chunk_multi_vector_ids(tmp_path) -> None:
    db = tmp_path / "fresh.db"
    open_index_database(db).close()
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_multi_vector_ids'",
        ).fetchall()
    assert rows == [("chunk_multi_vector_ids",)]


def test_indices_present(tmp_path) -> None:
    db = tmp_path / "idx.db"
    open_index_database(db).close()
    with sqlite3.connect(db) as conn:
        idx = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='chunk_multi_vector_ids'",
            )
        }
    assert "idx_cmv_plaid_doc_id" in idx
    assert "idx_cmv_package" in idx


def test_v5_db_is_migrated(tmp_path) -> None:
    """v5 -> v6 triggers the wipe-and-recreate path."""
    db = tmp_path / "old.db"
    # Synthesize a v5 DB by writing user_version = 5 and an empty packages table.
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE packages(name TEXT PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 5")
    open_index_database(db).close()
    with sqlite3.connect(db) as conn:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE name='chunk_multi_vector_ids'",
        ).fetchall()
    assert ver == SCHEMA_VERSION
    assert rows == [("chunk_multi_vector_ids",)]
