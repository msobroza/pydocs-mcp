"""Schema v10: additive ``node_scores`` table (in-degree / PageRank / community).

v10 is purely additive — it creates the empty ``node_scores`` table the graph
rerank steps read. Unlike v9 it forces no wholesale re-extraction: a v9 → v10
upgrade must preserve every row and keep DEPENDENCY ``packages.content_hash``
values intact (the next index populates node_scores via the post-index
recompute, but no re-embed is needed). Since v16 the project package alone has
its hash cleared, so one re-extraction fills the branch dimension's tables.
"""

import sqlite3
from pathlib import Path

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database


def test_schema_version_is_10() -> None:
    assert SCHEMA_VERSION >= 10


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def test_v9_to_v10_adds_node_scores_additively(tmp_path: Path) -> None:
    db_path = tmp_path / "v9.db"
    open_index_database(db_path).close()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO packages (name, version, content_hash) VALUES (?, ?, ?)",
        ("demo", "1.0.0", "keepme"),
    )
    conn.execute(
        "INSERT INTO packages (name, version, content_hash) VALUES (?, ?, ?)",
        ("__project__", "0.1.0", "clearme"),
    )
    conn.execute(
        "INSERT INTO chunks (package, module, title, text, origin, content_hash, "
        "qualified_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("demo", "demo.mod", "def foo()", "def foo(): ...", "dep", "chash", "demo.mod.foo"),
    )
    # Pretend it's an existing v9 cache (node_scores absent).
    conn.execute("DROP TABLE node_scores")
    conn.execute("PRAGMA user_version = 9")
    conn.commit()
    conn.close()

    # Reopen via the migration path (v9 → v10).
    open_index_database(db_path).close()

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert _table_exists(conn, "node_scores")
        # Additive for dependencies: content_hash survives (no re-extraction).
        pkg = conn.execute(
            "SELECT name, version, content_hash FROM packages WHERE name='demo'"
        ).fetchone()
        assert pkg == ("demo", "1.0.0", "keepme")
        # The PROJECT package's hash is cleared, so the next index re-extracts
        # it once and fills the v16 branch tables; without it the package-level
        # hash skip would leave them empty forever.
        project = conn.execute(
            "SELECT version, content_hash FROM packages WHERE name='__project__'"
        ).fetchone()
        assert project == ("0.1.0", None)
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    finally:
        conn.close()


def test_node_scores_survives_drift_recovery_reopen(tmp_path: Path) -> None:
    # A current (v10) DB reopened re-runs the additive sweep idempotently and
    # keeps any rows that were written.
    db_path = tmp_path / "v10.db"
    open_index_database(db_path).close()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO node_scores (package, qualified_name, in_degree, pagerank, community) "
        "VALUES (?, ?, ?, ?, ?)",
        ("demo", "demo.mod.foo", 3, 0.5, 1),
    )
    conn.commit()
    conn.close()

    open_index_database(db_path).close()  # drift-recovery reopen

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        row = conn.execute(
            "SELECT in_degree, pagerank, community FROM node_scores WHERE qualified_name='demo.mod.foo'"
        ).fetchone()
        assert row == (3, 0.5, 1)
    finally:
        conn.close()
