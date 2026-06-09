"""Schema v9: no structural change.

v9 exists to repopulate ``document_trees`` with two extraction enrichments
that neither the chunk nor the node ``content_hash`` covers:

- the FULL multi-line ``def`` / ``class`` header in ``extra_metadata["signature"]``
  (previously only the first physical line was captured), and
- decorator call arguments in ``extra_metadata["decorators"]``
  (``@app.route('/login')`` instead of the bare ``@app.route``).

Both land in the ``document_trees`` JSON blob, which no ``content_hash``
covers, so an unchanged-files reindex would otherwise skip the package and
never refresh its trees.

The v→v9 migration is **non-destructive**: it clears ``packages.content_hash``
so the next index re-extracts every package (rewriting trees WITH the richer
metadata), while keeping chunks + the ``.tq`` / multi-vector sidecars in
place — the chunk ``content_hash`` is unchanged, so the diff skips
re-embedding, and the stale trees keep serving until re-extraction replaces
them.
"""

import sqlite3
from pathlib import Path

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database


def test_schema_version_is_9() -> None:
    assert SCHEMA_VERSION == 9


def test_v8_to_v9_clears_content_hash_but_preserves_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "v8.db"
    # Build a current-structure DB, then stamp it back to v8 with data, to
    # simulate an existing v8 cache.
    open_index_database(db_path).close()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO packages (name, version, content_hash) VALUES (?, ?, ?)",
        ("demo", "1.0.0", "abc123"),
    )
    conn.execute(
        "INSERT INTO chunks (package, module, title, text, origin, content_hash, "
        "qualified_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("demo", "demo.mod", "def foo()", "def foo(): ...", "dep", "chash", "demo.mod.foo"),
    )
    conn.execute(
        "INSERT INTO document_trees (package, module, tree_json) VALUES (?, ?, ?)",
        ("demo", "demo.mod", "{}"),
    )
    conn.execute("PRAGMA user_version = 8")
    conn.commit()
    conn.close()

    # Reopen via the migration path (v8 → v9).
    open_index_database(db_path).close()

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        # Non-destructive: the package row survives (name + version intact)…
        pkg = conn.execute(
            "SELECT name, version, content_hash FROM packages WHERE name='demo'"
        ).fetchone()
        assert pkg[:2] == ("demo", "1.0.0")
        # …but content_hash is cleared so the next index re-extracts it.
        assert pkg[2] is None
        # Chunks + their trees survive (no re-embed, no empty-tree window).
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM document_trees").fetchone()[0] == 1
    finally:
        conn.close()
