"""Schema v8: no structural change.

v8 exists to repopulate ``document_trees`` with the pageindex decorators
(``extra_metadata["decorators"]``) that landed with signature/docstring
enrichment. Neither the chunk nor the node ``content_hash`` covers
``extra_metadata``, so an unchanged-files reindex would otherwise skip the
package and never refresh its trees.

The v→v8 migration is **non-destructive**: it clears ``packages.content_hash``
so the next index re-extracts every package (rewriting trees WITH decorators),
while keeping chunks + the ``.tq`` / multi-vector sidecars in place — the chunk
``content_hash`` is unchanged, so the diff skips re-embedding, and the stale
trees keep serving until re-extraction replaces them.
"""

import sqlite3
from pathlib import Path

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database


def test_schema_version_is_8() -> None:
    assert SCHEMA_VERSION == 10


def test_v7_to_v8_clears_content_hash_but_preserves_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "v7.db"
    # Build a current-structure DB, then stamp it back to v7 with data, to
    # simulate an existing v7 cache.
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
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    conn.close()

    # Reopen via the migration path (v7 → v8).
    open_index_database(db_path).close()

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
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
