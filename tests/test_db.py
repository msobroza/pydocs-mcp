"""Tests for database operations (db.py)."""

import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import (
    SCHEMA_VERSION,
    cache_path_for_project,
    clear_all_packages,
    get_stored_content_hash,
    open_index_database,
    rebuild_fulltext_index,
    remove_package,
)


@pytest.fixture
def db(tmp_path):
    return open_index_database(tmp_path / "test.db")


@pytest.fixture
def db_with_package(db):
    db.execute(
        "INSERT INTO packages(name,version,summary,homepage,dependencies,content_hash,origin) VALUES(?,?,?,?,?,?,?)",
        (
            "testpkg",
            "2.0",
            "A test package.",
            "https://example.com",
            '["requests"]',
            "testhash",
            "dependency",
        ),
    )
    db.execute(
        "INSERT INTO chunks(package,title,text,origin) VALUES(?,?,?,?)",
        (
            "testpkg",
            "Overview",
            "This is the overview of testpkg documentation.",
            "dependency_doc_file",
        ),
    )
    db.execute(
        "INSERT INTO module_members(package,module,kind,name,signature,docstring,parameters,return_annotation) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            "testpkg",
            "testpkg.core",
            "function",
            "compute",
            "(x: int)",
            "Compute something.",
            "[]",
            "int",
        ),
    )
    db.commit()
    return db


class TestDbPathFor:
    def test_returns_path_under_cache_dir(self, tmp_path):
        p = cache_path_for_project(tmp_path)
        assert ".pydocs-mcp" in str(p)

    def test_deterministic(self, tmp_path):
        assert cache_path_for_project(tmp_path) == cache_path_for_project(tmp_path)

    def test_different_projects_get_different_paths(self, tmp_path):
        a = tmp_path / "project_a"
        b = tmp_path / "project_b"
        a.mkdir()
        b.mkdir()
        assert cache_path_for_project(a) != cache_path_for_project(b)

    def test_includes_project_name(self, tmp_path):
        p = cache_path_for_project(tmp_path)
        assert tmp_path.resolve().name in p.name


class TestOpenDb:
    def test_creates_file(self, tmp_path):
        db_file = tmp_path / "test.db"
        conn = open_index_database(db_file)
        assert db_file.exists()
        conn.close()

    def test_creates_parent_dirs(self, tmp_path):
        db_file = tmp_path / "sub" / "dir" / "test.db"
        conn = open_index_database(db_file)
        assert db_file.exists()
        conn.close()

    def test_row_factory_is_row(self, db):
        assert db.row_factory == sqlite3.Row

    def test_packages_table_exists(self, db):
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='packages'"
        ).fetchone()
        assert row is not None

    def test_chunks_table_exists(self, db):
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks'"
        ).fetchone()
        assert row is not None

    def test_symbols_table_exists(self, db):
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='module_members'"
        ).fetchone()
        assert row is not None

    def test_fts_table_exists(self, db):
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
        ).fetchone()
        assert row is not None

    def test_wal_mode(self, db):
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_indexes_created(self, db):
        indexes = {
            r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert "ix_chunks_package" in indexes
        assert "ix_module_members_package" in indexes
        assert "ix_module_members_name" in indexes

    def test_idempotent(self, tmp_path):
        db_file = tmp_path / "test.db"
        conn1 = open_index_database(db_file)
        conn1.execute(
            "INSERT INTO packages(name,version,summary,homepage,dependencies,content_hash,origin) "
            "VALUES(?,?,?,?,?,?,?)",
            ("pkg1", "1.0", "test", "", "[]", "abc", "dependency"),
        )
        conn1.commit()
        conn1.close()

        conn2 = open_index_database(db_file)
        row = conn2.execute("SELECT * FROM packages WHERE name='pkg1'").fetchone()
        assert row is not None
        assert row["version"] == "1.0"
        conn2.close()


class TestClearPkg:
    def test_removes_target_package(self, db_with_package):
        remove_package(db_with_package, "testpkg")
        db_with_package.commit()
        assert (
            db_with_package.execute("SELECT * FROM packages WHERE name='testpkg'").fetchone()
            is None
        )

    def test_removes_chunks_for_package(self, db_with_package):
        remove_package(db_with_package, "testpkg")
        db_with_package.commit()
        assert (
            db_with_package.execute("SELECT * FROM chunks WHERE package='testpkg'").fetchone()
            is None
        )

    def test_removes_symbols_for_package(self, db_with_package):
        remove_package(db_with_package, "testpkg")
        db_with_package.commit()
        assert (
            db_with_package.execute(
                "SELECT * FROM module_members WHERE package='testpkg'"
            ).fetchone()
            is None
        )

    def test_leaves_other_packages(self, db_with_package):
        db_with_package.execute(
            "INSERT INTO packages(name,version,summary,homepage,dependencies,content_hash,origin) "
            "VALUES(?,?,?,?,?,?,?)",
            ("other", "1.0", "other pkg", "", "[]", "xyz", "dependency"),
        )
        db_with_package.commit()
        remove_package(db_with_package, "testpkg")
        db_with_package.commit()
        assert (
            db_with_package.execute("SELECT * FROM packages WHERE name='other'").fetchone()
            is not None
        )

    def test_removes_document_trees_for_package(self, db_with_package):
        """remove_package must clear document_trees rows for that package —
        without this, stale trees survive a re-index and LookupService
        returns outdated payloads (sub-PR #5 §12.2)."""
        db_with_package.execute(
            "INSERT INTO document_trees(package, module, tree_json) VALUES(?,?,?)",
            ("testpkg", "testpkg.mod", "{}"),
        )
        db_with_package.commit()
        remove_package(db_with_package, "testpkg")
        db_with_package.commit()
        assert (
            db_with_package.execute(
                "SELECT * FROM document_trees WHERE package='testpkg'"
            ).fetchone()
            is None
        )

    def test_leaves_other_packages_document_trees(self, db_with_package):
        """Cross-package isolation: remove_package('testpkg') leaves other
        packages' tree rows intact."""
        db_with_package.execute(
            "INSERT INTO document_trees(package, module, tree_json) VALUES(?,?,?)",
            ("other", "other.mod", "{}"),
        )
        db_with_package.execute(
            "INSERT INTO document_trees(package, module, tree_json) VALUES(?,?,?)",
            ("testpkg", "testpkg.mod", "{}"),
        )
        db_with_package.commit()
        remove_package(db_with_package, "testpkg")
        db_with_package.commit()
        assert (
            db_with_package.execute("SELECT * FROM document_trees WHERE package='other'").fetchone()
            is not None
        )


class TestClearAll:
    def test_clears_everything(self, db_with_package):
        # Seed a tree row so the assertion below is meaningful — without
        # this, document_trees count is already 0 from fixture setup.
        db_with_package.execute(
            "INSERT INTO document_trees(package, module, tree_json) VALUES(?,?,?)",
            ("testpkg", "testpkg.mod", "{}"),
        )
        db_with_package.commit()
        clear_all_packages(db_with_package)
        assert db_with_package.execute("SELECT count(*) FROM packages").fetchone()[0] == 0
        assert db_with_package.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
        assert db_with_package.execute("SELECT count(*) FROM module_members").fetchone()[0] == 0
        # document_trees must be cleared too (sub-PR #5 §12.2) — otherwise
        # a fresh re-index reads stale tree payloads for the cleared
        # packages.
        assert db_with_package.execute("SELECT count(*) FROM document_trees").fetchone()[0] == 0


class TestRebuildFts:
    def test_fts_search_works_after_rebuild(self, db_with_package):
        rebuild_fulltext_index(db_with_package)
        rows = db_with_package.execute(
            "SELECT * FROM chunks_fts WHERE chunks_fts MATCH ?", ('"overview"',)
        ).fetchall()
        assert len(rows) >= 1

    def test_rebuild_on_empty_db(self, db):
        rebuild_fulltext_index(db)

    def test_fts_reflects_new_data(self, db):
        db.execute(
            "INSERT INTO chunks(package,title,text,origin) VALUES(?,?,?,?)",
            (
                "pkg",
                "Title",
                "unique searchable content for testing purposes",
                "dependency_doc_file",
            ),
        )
        db.commit()
        rebuild_fulltext_index(db)
        rows = db.execute(
            "SELECT * FROM chunks_fts WHERE chunks_fts MATCH ?", ('"searchable"',)
        ).fetchall()
        assert len(rows) == 1


class TestGetCachedHash:
    def test_returns_none_for_missing(self, db):
        assert get_stored_content_hash(db, "nonexistent") is None

    def test_returns_hash_for_existing(self, db):
        db.execute(
            "INSERT INTO packages(name,version,summary,homepage,dependencies,content_hash,origin) "
            "VALUES(?,?,?,?,?,?,?)",
            ("mypkg", "1.0", "", "", "[]", "abc123", "dependency"),
        )
        db.commit()
        assert get_stored_content_hash(db, "mypkg") == "abc123"

    def test_returns_none_when_hash_is_null(self, db):
        db.execute(
            "INSERT INTO packages(name,version,summary,homepage,dependencies,content_hash,origin) "
            "VALUES(?,?,?,?,?,?,?)",
            ("mypkg", "1.0", "", "", "[]", None, "dependency"),
        )
        db.commit()
        assert get_stored_content_hash(db, "mypkg") is None


from pydocs_mcp.db import build_connection_provider


async def test_build_connection_provider_opens_valid_db(tmp_path):
    db_file = tmp_path / "factory.db"
    conn = open_index_database(db_file)
    conn.close()

    provider = build_connection_provider(db_file)
    import sqlite3

    async with provider.acquire() as c:
        assert c.row_factory is sqlite3.Row
        tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"packages", "chunks", "module_members"}.issubset(tables)


class TestSchemaV3:
    """Schema v3: document_trees table + chunks.content_hash + packages.local_path."""

    def test_fresh_db_is_v3(self, tmp_path):
        # Schema is now v6 (additive on top of v5/v4/v3). The v3 invariants
        # (document_trees / content_hash / local_path) still hold; the
        # version stamp simply moved forward.
        conn = open_index_database(tmp_path / "v3.db")
        try:
            assert SCHEMA_VERSION == 8
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        finally:
            conn.close()

    def test_document_trees_primary_key_enforced(self, db):
        db.execute(
            "INSERT INTO document_trees(package, module, tree_json) VALUES(?,?,?)",
            ("pkg", "pkg.mod", "{}"),
        )
        db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO document_trees(package, module, tree_json) VALUES(?,?,?)",
                ("pkg", "pkg.mod", "{}"),
            )
            db.commit()

    def test_chunks_has_content_hash_column(self, db):
        cols = {r["name"] for r in db.execute("PRAGMA table_info(chunks)").fetchall()}
        assert "content_hash" in cols

    def test_packages_has_local_path_column(self, db):
        cols = {r["name"] for r in db.execute("PRAGMA table_info(packages)").fetchall()}
        assert "local_path" in cols

    def test_v2_to_v3_migration_preserves_rows(self, tmp_path):
        """A pre-existing v2 DB keeps its packages/chunks/module_members rows
        when upgraded in place — the migration adds columns/tables without
        rewriting unchanged data."""
        db_file = tmp_path / "legacy.db"

        # Hand-build a v2 DB: create the v2 tables directly, set user_version = 2.
        legacy = sqlite3.connect(str(db_file))
        legacy.executescript(
            """
            CREATE TABLE packages (
                name TEXT PRIMARY KEY, version TEXT, summary TEXT,
                homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT
            );
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY, package TEXT,
                title TEXT, text TEXT, origin TEXT
            );
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                title, text, package,
                content=chunks, content_rowid=id,
                tokenize='porter unicode61'
            );
            CREATE TABLE module_members (
                id INTEGER PRIMARY KEY, package TEXT, module TEXT,
                name TEXT, kind TEXT, signature TEXT,
                return_annotation TEXT, parameters TEXT, docstring TEXT
            );
            """
        )
        legacy.execute("PRAGMA user_version = 2")
        legacy.execute(
            "INSERT INTO packages(name,version,summary,homepage,dependencies,content_hash,origin) "
            "VALUES(?,?,?,?,?,?,?)",
            ("legacy_pkg", "1.0", "legacy", "", "[]", "h1", "dependency"),
        )
        legacy.execute(
            "INSERT INTO chunks(package,title,text,origin) VALUES(?,?,?,?)",
            ("legacy_pkg", "Title", "legacy body", "dependency_doc_file"),
        )
        legacy.commit()
        legacy.close()

        # Reopen via the real entry point — should soft-migrate forward.
        # Walks v2 → … → v7 in a single open.
        migrated = open_index_database(db_file)
        try:
            assert migrated.execute("PRAGMA user_version").fetchone()[0] == 8

            # Old rows must survive.
            pkg = migrated.execute(
                "SELECT name, version FROM packages WHERE name=?",
                ("legacy_pkg",),
            ).fetchone()
            assert pkg is not None
            assert pkg["version"] == "1.0"

            chunk = migrated.execute(
                "SELECT title, text FROM chunks WHERE package=?",
                ("legacy_pkg",),
            ).fetchone()
            assert chunk is not None
            assert chunk["title"] == "Title"

            # New columns must be queryable (NULL for rows written pre-migration).
            pkg_local = migrated.execute(
                "SELECT local_path FROM packages WHERE name=?",
                ("legacy_pkg",),
            ).fetchone()
            assert pkg_local["local_path"] is None

            chunk_hash = migrated.execute(
                "SELECT content_hash FROM chunks WHERE package=?",
                ("legacy_pkg",),
            ).fetchone()
            assert chunk_hash["content_hash"] is None

            # New table must exist.
            row = migrated.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='document_trees'"
            ).fetchone()
            assert row is not None
            # Migration MUST also create ``ix_chunks_module`` — the
            # fresh-DB DDL has it, so without an explicit add here a
            # migrated DB would scan chunks for every module filter
            # until the next destructive rebuild.
            idx = migrated.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_chunks_module'"
            ).fetchone()
            assert idx is not None, (
                "v2->v3 migration must create ix_chunks_module so module "
                "filter queries don't full-scan the chunks table"
            )
        finally:
            migrated.close()

    def test_v3_main_shape_db_gets_document_trees_and_columns_on_open(
        self,
        tmp_path,
    ):
        """A v3-stamped DB written by an earlier code-line that lacked
        document_trees + content_hash + local_path (rebase artefact between
        sub-PR #5 and sub-PR #6) must gain them on the next open without
        losing existing rows. The migration is idempotent and rerun on
        every v3 open."""
        db = tmp_path / "main_v3.db"
        raw = sqlite3.connect(str(db))
        raw.executescript(
            """
            CREATE TABLE packages (
                name TEXT PRIMARY KEY, version TEXT, summary TEXT,
                homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT
            );
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY, package TEXT, module TEXT DEFAULT '',
                title TEXT, text TEXT, origin TEXT
            );
            CREATE TABLE module_members (
                id INTEGER PRIMARY KEY, package TEXT, module TEXT,
                name TEXT, kind TEXT, signature TEXT,
                return_annotation TEXT, parameters TEXT, docstring TEXT
            );
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                title, text, package, content=chunks, content_rowid=id,
                tokenize='porter unicode61'
            );
            PRAGMA user_version = 3;
            """
        )
        raw.execute(
            "INSERT INTO packages "
            "(name, version, summary, homepage, dependencies, content_hash, origin)"
            " VALUES ('preserved', '1.0', '', '', '[]', 'h', 'dependency')"
        )
        raw.commit()
        raw.close()

        conn = open_index_database(db)
        try:
            # New table exists.
            assert (
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='document_trees'"
                ).fetchone()
                is not None
            )
            # New columns exist.
            chunk_cols = {r[1] for r in conn.execute("PRAGMA table_info(chunks)").fetchall()}
            assert "content_hash" in chunk_cols
            pkg_cols = {r[1] for r in conn.execute("PRAGMA table_info(packages)").fetchall()}
            assert "local_path" in pkg_cols
            # Existing row preserved.
            row = conn.execute("SELECT name FROM packages WHERE name='preserved'").fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_v3_open_open_open_is_idempotent(self, tmp_path):
        """Opening a v3 DB multiple times must not duplicate columns or
        schemas — ``_try_add_column`` swallows duplicate-column errors and
        ``CREATE TABLE IF NOT EXISTS`` is a no-op when the table is present."""
        db = tmp_path / "idempotent.db"
        for _ in range(3):
            conn = open_index_database(db)
            conn.close()

        conn = open_index_database(db)
        try:
            chunk_cols = [r[1] for r in conn.execute("PRAGMA table_info(chunks)").fetchall()]
            assert chunk_cols.count("content_hash") == 1
            pkg_cols = [r[1] for r in conn.execute("PRAGMA table_info(packages)").fetchall()]
            assert pkg_cols.count("local_path") == 1
        finally:
            conn.close()


def test_schema_version_is_4_after_open(tmp_path):
    """Schema version is 6 — additive bump for chunk_multi_vector_ids."""
    db = tmp_path / "x.db"
    conn = open_index_database(db)
    try:
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert ver == 8
    assert SCHEMA_VERSION == 8


def test_node_references_table_created_on_fresh_db(tmp_path):
    """Fresh DB DDL creates node_references + the 3 indices."""
    db = tmp_path / "x.db"
    conn = open_index_database(db)
    try:
        # PRAGMA table_info validates column shape.
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(node_references)").fetchall()]
        assert cols == [
            "from_package",
            "from_node_id",
            "to_name",
            "to_node_id",
            "kind",
        ]
        # 3 secondary indices.
        idx = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='node_references'"
            ).fetchall()
        }
        assert "ix_refs_from" in idx
        assert "ix_refs_to_name" in idx
        assert "ix_refs_to_node" in idx
    finally:
        conn.close()


def test_v3_to_v4_migration_preserves_existing_rows(tmp_path):
    """v3 → v4 must be ADDITIVE — packages/chunks/module_members/document_trees
    rows survive the bump. Verifies spec Decision 6.
    """
    import sqlite3

    db = tmp_path / "x.db"
    # Hand-craft a v3 DB stamped at user_version=3 with one row in each table.
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE packages (
            name TEXT PRIMARY KEY, version TEXT, summary TEXT,
            homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
            local_path TEXT
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
            package TEXT NOT NULL, module TEXT NOT NULL,
            tree_json TEXT NOT NULL, content_hash TEXT, updated_at REAL,
            PRIMARY KEY (package, module)
        );
        PRAGMA user_version = 3;
    """)
    conn.execute(
        "INSERT INTO packages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("pkg", "1.0", "s", "h", "[]", "ch", "DEPENDENCY", None),
    )
    conn.execute(
        "INSERT INTO chunks (package, module, title, text, origin, content_hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("pkg", "pkg.mod", "T", "body", "src", "ch"),
    )
    conn.commit()
    conn.close()

    # Now open through the production path — must migrate and PRESERVE rows.
    # Walks v3 → … → v7 in a single open.
    conn = open_index_database(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        # The package row survives.
        row = conn.execute("SELECT name FROM packages WHERE name='pkg'").fetchone()
        assert row is not None
        # The chunk row survives.
        cnt = conn.execute("SELECT COUNT(*) AS c FROM chunks WHERE package='pkg'").fetchone()["c"]
        assert cnt == 1
        # node_references exists and is empty.
        cnt = conn.execute("SELECT COUNT(*) AS c FROM node_references").fetchone()["c"]
        assert cnt == 0
    finally:
        conn.close()


def test_v4_open_open_open_is_idempotent(tmp_path):
    """Spec AC #2: opening a v4 DB N times never duplicates anything.

    Mirrors test_v3_open_open_open_is_idempotent (if it exists). Re-runs
    the additive sweep — CREATE TABLE IF NOT EXISTS + CREATE INDEX IF
    NOT EXISTS, no-op on each subsequent open.
    """
    db = tmp_path / "x.db"
    open_index_database(db).close()
    open_index_database(db).close()
    conn = open_index_database(db)
    try:
        # Still exactly one node_references table, exactly 3 named secondary
        # indices (filtering out the implicit ``sqlite_autoindex_*`` index
        # SQLite creates for the composite PRIMARY KEY).
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='node_references'"
        ).fetchall()
        assert len(tbl) == 1
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='node_references' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        assert len(idx) == 3
    finally:
        conn.close()


def test_drift_recovery_recreates_missing_node_references(tmp_path):
    """AC #3: opening a v4-stamped DB with the node_references table
    manually DROPPED triggers the additive sweep on next open."""
    import sqlite3

    db = tmp_path / "x.db"
    open_index_database(db).close()  # creates v4 schema

    # Manually drop node_references — simulate drift / partial DB damage.
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE node_references")
    conn.commit()
    conn.close()

    # Open again — repair sweep runs.
    conn = open_index_database(db)
    try:
        tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='node_references'"
        ).fetchall()
        assert len(tbl) == 1
    finally:
        conn.close()


def test_remove_package_clears_node_references(tmp_path):
    """AC #13: remove_package deletes node_references rows for that package."""
    import sqlite3
    from pydocs_mcp.db import remove_package

    db = tmp_path / "x.db"
    conn = open_index_database(db)
    try:
        conn.execute(
            "INSERT INTO node_references VALUES (?, ?, ?, ?, ?)",
            ("pkg", "pkg.mod.fn", "other", None, "calls"),
        )
        conn.execute(
            "INSERT INTO node_references VALUES (?, ?, ?, ?, ?)",
            ("other_pkg", "other_pkg.x", "z", None, "calls"),
        )
        conn.commit()
        remove_package(conn, "pkg")
        rows = conn.execute("SELECT from_package FROM node_references").fetchall()
        assert [r["from_package"] for r in rows] == ["other_pkg"]
    finally:
        conn.close()


def test_clear_all_packages_clears_node_references(tmp_path):
    """AC #14: clear_all_packages wipes node_references entirely."""
    from pydocs_mcp.db import clear_all_packages

    db = tmp_path / "x.db"
    conn = open_index_database(db)
    try:
        conn.execute(
            "INSERT INTO node_references VALUES (?, ?, ?, ?, ?)",
            ("pkg", "pkg.mod.fn", "other", None, "calls"),
        )
        conn.commit()
        clear_all_packages(conn)
        cnt = conn.execute("SELECT COUNT(*) AS c FROM node_references").fetchone()["c"]
        assert cnt == 0
    finally:
        conn.close()
