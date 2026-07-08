"""``remove_package`` / ``clear_all_packages`` must keep ``chunks_fts`` in sync.

``chunks_fts`` is an external-content FTS5 table (``content=chunks``): it does
NOT observe plain ``DELETE FROM chunks``. The package-removal helpers used to
delete content rows only, leaving orphaned index entries whose damage is
mostly silent:

- once SQLite reuses a deleted rowid (``chunks.id`` has no AUTOINCREMENT),
  queries for the OLD chunk's tokens match the NEW unrelated chunk — wrong
  search results with no error;
- FTS5's own ``integrity-check`` command reports the database as malformed;
- any query shape that reads FTS columns raises ``missing row N from content
  table``.

The production heal (``rebuild_fulltext_index``) runs only at the end of a
full index pass, so a crash in between persisted the corruption. The helpers
must instead sync the FTS index inside the same transaction as the deletes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pydocs_mcp.db import clear_all_packages, open_index_database, remove_package


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_index_database(tmp_path / "fts_sync.db")
    conn.executemany(
        "INSERT INTO chunks (package, title, text) VALUES (?, ?, ?)",
        [
            ("demo", "routing", "the router maps paths to handlers"),
            ("other", "parsing", "the yaml parser reads config files"),
        ],
    )
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()
    return conn


def _fts_integrity_ok(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES('integrity-check', 1)")
    except sqlite3.DatabaseError:
        return False
    return True


def _production_match(conn: sqlite3.Connection, token: str) -> list[sqlite3.Row]:
    """The JOIN shape SqliteLexicalStore.text_search uses."""
    return conn.execute(
        "SELECT c.id, c.title, c.package FROM chunks_fts m JOIN chunks c ON c.id = m.rowid "
        "WHERE chunks_fts MATCH ?",
        (token,),
    ).fetchall()


def test_remove_package_keeps_fts_integrity(tmp_path: Path) -> None:
    conn = _make_db(tmp_path)
    remove_package(conn, "demo")
    conn.commit()
    assert _fts_integrity_ok(conn), "orphaned FTS entries survived remove_package"


def test_remove_package_prevents_stale_token_matches_after_rowid_reuse(tmp_path: Path) -> None:
    """The silent-corruption repro: a new chunk reusing the deleted rowid
    must NOT be served for the old chunk's tokens."""
    conn = _make_db(tmp_path)
    demo_id = conn.execute("SELECT id FROM chunks WHERE package='demo'").fetchone()[0]
    remove_package(conn, "demo")
    conn.commit()

    conn.execute(
        "INSERT INTO chunks (id, package, title, text) "
        "VALUES (?, 'reuse', 'totally unrelated', 'numpy array broadcasting')",
        (demo_id,),
    )
    conn.commit()

    rows = _production_match(conn, "router")
    assert rows == [], (
        f"stale-token query matched the rowid-reusing chunk: "
        f"{[(r['id'], r['title']) for r in rows]}"
    )


def test_remove_package_leaves_other_packages_searchable(tmp_path: Path) -> None:
    """The FTS sync must be surgical — other packages' index entries survive."""
    conn = _make_db(tmp_path)
    remove_package(conn, "demo")
    conn.commit()
    rows = _production_match(conn, "parser")
    assert [r["package"] for r in rows] == ["other"]


def test_remove_package_tolerates_never_rebuilt_fts_index(tmp_path: Path) -> None:
    """Chunks inserted but not yet indexed (the rebuild runs only at the end
    of a full pass) make FTS5's per-row 'delete' command raise "malformed" —
    remove_package must fall back to a rebuild instead of crashing, and leave
    the index coherent with the surviving rows."""
    conn = open_index_database(tmp_path / "stale_index.db")
    conn.executemany(
        "INSERT INTO chunks (package, title, text) VALUES (?, ?, ?)",
        [
            ("demo", "routing", "the router maps paths to handlers"),
            ("other", "parsing", "the yaml parser reads config files"),
        ],
    )
    conn.commit()  # NOTE: no chunks_fts rebuild — index is empty/stale

    remove_package(conn, "demo")
    conn.commit()

    assert _fts_integrity_ok(conn)
    rows = _production_match(conn, "parser")
    assert [r["package"] for r in rows] == ["other"], (
        "fallback rebuild must index the surviving rows"
    )


def test_clear_all_packages_keeps_fts_integrity(tmp_path: Path) -> None:
    conn = _make_db(tmp_path)
    clear_all_packages(conn)
    assert _fts_integrity_ok(conn), "orphaned FTS entries survived clear_all_packages"
    assert _production_match(conn, "router") == []
    assert _production_match(conn, "parser") == []
