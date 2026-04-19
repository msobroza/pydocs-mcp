"""Tests for database operations (db.py)."""
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import (
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
        ("testpkg", "2.0", "A test package.", "https://example.com", '["requests"]', "testhash", "dependency"),
    )
    db.execute(
        "INSERT INTO chunks(package,title,text,origin) VALUES(?,?,?,?)",
        ("testpkg", "Overview", "This is the overview of testpkg documentation.", "dependency_doc_file"),
    )
    db.execute(
        "INSERT INTO module_members(package,module,kind,name,signature,docstring,parameters,return_annotation) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("testpkg", "testpkg.core", "function", "compute", "(x: int)", "Compute something.", "[]", "int"),
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
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
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
        assert db_with_package.execute(
            "SELECT * FROM packages WHERE name='testpkg'"
        ).fetchone() is None

    def test_removes_chunks_for_package(self, db_with_package):
        remove_package(db_with_package, "testpkg")
        db_with_package.commit()
        assert db_with_package.execute(
            "SELECT * FROM chunks WHERE package='testpkg'"
        ).fetchone() is None

    def test_removes_symbols_for_package(self, db_with_package):
        remove_package(db_with_package, "testpkg")
        db_with_package.commit()
        assert db_with_package.execute(
            "SELECT * FROM module_members WHERE package='testpkg'"
        ).fetchone() is None

    def test_leaves_other_packages(self, db_with_package):
        db_with_package.execute(
            "INSERT INTO packages(name,version,summary,homepage,dependencies,content_hash,origin) "
            "VALUES(?,?,?,?,?,?,?)",
            ("other", "1.0", "other pkg", "", "[]", "xyz", "dependency"),
        )
        db_with_package.commit()
        remove_package(db_with_package, "testpkg")
        db_with_package.commit()
        assert db_with_package.execute(
            "SELECT * FROM packages WHERE name='other'"
        ).fetchone() is not None


class TestClearAll:
    def test_clears_everything(self, db_with_package):
        clear_all_packages(db_with_package)
        assert db_with_package.execute("SELECT count(*) FROM packages").fetchone()[0] == 0
        assert db_with_package.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
        assert db_with_package.execute("SELECT count(*) FROM module_members").fetchone()[0] == 0


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
            ("pkg", "Title", "unique searchable content for testing purposes", "dependency_doc_file"),
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
