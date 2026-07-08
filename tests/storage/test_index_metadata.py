"""index_metadata table: round-trip, v10->v11 migration, legacy fallback."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database
from pydocs_mcp.storage.index_metadata import (
    IndexMetadata,
    read_index_metadata,
    write_index_metadata,
)


def _meta(**kw) -> IndexMetadata:
    base = dict(
        project_name="webapp",
        project_root="/home/me/webapp",
        embedding_provider="fastembed",
        embedding_model="BAAI/bge-small-en-v1.5",
        embedding_dim=384,
        pipeline_hash="abc123",
        indexed_at=1000.0,
    )
    base.update(kw)
    return IndexMetadata(**base)


def test_fresh_db_has_empty_index_metadata(tmp_path: Path) -> None:
    conn = open_index_database(tmp_path / "x.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert read_index_metadata(conn) is None  # empty table -> no row


def test_write_read_round_trip(tmp_path: Path) -> None:
    conn = open_index_database(tmp_path / "x.db")
    write_index_metadata(conn, _meta())
    assert read_index_metadata(conn) == _meta()


def test_write_is_single_row_upsert(tmp_path: Path) -> None:
    conn = open_index_database(tmp_path / "x.db")
    write_index_metadata(conn, _meta(indexed_at=1.0))
    write_index_metadata(conn, _meta(indexed_at=2.0, project_name="other"))
    assert conn.execute("SELECT COUNT(*) FROM index_metadata").fetchone()[0] == 1
    got = read_index_metadata(conn)
    assert got is not None and got.indexed_at == 2.0 and got.project_name == "other"


def test_v10_db_migrates_to_v11_additively(tmp_path: Path) -> None:
    # Build a real v11 db, then simulate a legacy v10 db: drop index_metadata and
    # stamp user_version=10 (v10 had node_scores but no index_metadata).
    db = tmp_path / "legacy.db"
    conn = open_index_database(db)
    conn.execute("INSERT INTO packages(name, embedding_model) VALUES('__project__', 'bge')")
    conn.execute("DROP TABLE index_metadata")
    conn.execute("PRAGMA user_version = 10")
    conn.commit()
    conn.close()

    conn2 = open_index_database(db)  # reopen -> migrate 10 -> 11
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert read_index_metadata(conn2) is None  # table created, empty
    # data preserved (additive migration, no wipe)
    assert conn2.execute("SELECT embedding_model FROM packages").fetchone()[0] == "bge"


def test_single_row_check_constraint(tmp_path: Path) -> None:
    conn = open_index_database(tmp_path / "x.db")
    write_index_metadata(conn, _meta())
    # id is pinned to 1 by the CHECK; a second explicit id must fail.
    try:
        conn.execute("INSERT INTO index_metadata(id, project_name) VALUES(2, 'nope')")
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised


def test_read_returns_none_on_genuinely_pre_v11_db_without_migration(tmp_path: Path) -> None:
    """A pre-v11 db opened via plain sqlite3.connect (no open_index_database) has
    no ``index_metadata`` table at all — the docstring promises ``None`` here (that
    is what ``legacy_fallback`` exists for), but a bare SELECT used to raise
    ``sqlite3.OperationalError: no such table: index_metadata`` instead. This is
    exactly what ``build_freshness_probe._read`` does (storage/factories.py) and
    what any multi-repo loader reading a sibling repo's db would do.
    """
    db = tmp_path / "legacy_no_migration.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE packages (name TEXT PRIMARY KEY, embedding_model TEXT)")
    conn.execute("INSERT INTO packages(name, embedding_model) VALUES('__project__', 'bge')")
    conn.commit()

    assert read_index_metadata(conn) is None
    conn.close()


def test_legacy_fallback_dim_unknown() -> None:
    meta = IndexMetadata.legacy_fallback(project_name="p", embedding_model="bge")
    assert meta.embedding_dim == -1 and meta.indexed_at == 0.0
    # unknown dim -> only the model name gates matching
    assert meta.embedder_matches(model="bge", dim=384)
    assert not meta.embedder_matches(model="other", dim=384)


def test_embedder_matches_checks_model_and_dim() -> None:
    meta = _meta(embedding_model="bge", embedding_dim=384)
    assert meta.embedder_matches(model="bge", dim=384)
    assert not meta.embedder_matches(model="bge", dim=768)  # dim mismatch
    assert not meta.embedder_matches(model="qwen", dim=384)  # model mismatch
