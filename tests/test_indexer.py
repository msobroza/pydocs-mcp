"""Tests for indexing logic (indexer.py)."""
import os
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database, rebuild_fulltext_index
from pydocs_mcp.indexer import (
    _extract_from_source_files,
    _persist_dependency,
    find_site_packages_root,
    index_project_source,
)


@pytest.fixture
def project_dir(tmp_path):
    """Create a minimal Python project for indexing."""
    src = tmp_path / "myproject"
    src.mkdir()
    (src / "__init__.py").write_text('"""My project package."""\n')
    (src / "core.py").write_text(
        '"""Core module with useful functions."""\n\n'
        'def compute(x: int, y: int) -> int:\n'
        '    """Add two numbers together."""\n'
        '    return x + y\n\n'
        'class Engine(object):\n'
        '    """The main compute engine for processing."""\n'
        '    pass\n'
    )
    (src / "utils.py").write_text(
        'def helper(s: str) -> str:\n'
        '    """A helper that formats strings nicely."""\n'
        '    return s.strip()\n'
    )
    (src / "internal.py").write_text(
        'def _private():\n'
        '    """Should not appear in symbols."""\n'
        '    pass\n'
    )
    return src


@pytest.fixture
def db(tmp_path):
    return open_index_database(tmp_path / "test.db")


class TestParseSourceFiles:
    def test_extracts_chunks_and_symbols(self, project_dir):
        py_files = sorted(str(p) for p in project_dir.rglob("*.py"))
        chunks, syms = _extract_from_source_files("mypkg", py_files, str(project_dir))
        assert len(chunks) > 0
        assert len(syms) > 0

    def test_symbol_names(self, project_dir):
        py_files = sorted(str(p) for p in project_dir.rglob("*.py"))
        _, syms = _extract_from_source_files("mypkg", py_files, str(project_dir))
        names = {s[2] for s in syms}
        assert "compute" in names
        assert "Engine" in names
        assert "helper" in names

    def test_skips_private_symbols(self, project_dir):
        py_files = sorted(str(p) for p in project_dir.rglob("*.py"))
        _, syms = _extract_from_source_files("mypkg", py_files, str(project_dir))
        names = {s[2] for s in syms}
        assert "_private" not in names

    def test_extracts_module_docstrings(self, project_dir):
        py_files = sorted(str(p) for p in project_dir.rglob("*.py"))
        chunks, _ = _extract_from_source_files("mypkg", py_files, str(project_dir))
        doc_chunks = [c for c in chunks if c[3] == "project_doc"]
        assert len(doc_chunks) > 0

    def test_empty_file_list(self):
        chunks, syms = _extract_from_source_files("mypkg", [], ".")
        assert chunks == []
        assert syms == []

    def test_kind_prefix(self, project_dir):
        py_files = sorted(str(p) for p in project_dir.rglob("*.py"))
        chunks, _ = _extract_from_source_files("mypkg", py_files, str(project_dir), "dep")
        kinds = {c[3] for c in chunks}
        assert all(k.startswith("dep") for k in kinds)


class TestIndexProject:
    def test_indexes_project_files(self, db, project_dir):
        index_project_source(db, project_dir)
        pkgs = db.execute("SELECT * FROM packages WHERE name='__project__'").fetchone()
        assert pkgs is not None
        assert pkgs["version"] == "local"

    def test_creates_chunks(self, db, project_dir):
        index_project_source(db, project_dir)
        count = db.execute(
            "SELECT count(*) FROM chunks WHERE package='__project__'"
        ).fetchone()[0]
        assert count > 0

    def test_creates_symbols(self, db, project_dir):
        index_project_source(db, project_dir)
        count = db.execute(
            "SELECT count(*) FROM module_members WHERE package='__project__'"
        ).fetchone()[0]
        assert count > 0

    def test_caching_skips_unchanged(self, db, project_dir):
        index_project_source(db, project_dir)
        chunks_before = db.execute("SELECT count(*) FROM chunks").fetchone()[0]

        index_project_source(db, project_dir)
        chunks_after = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        assert chunks_after == chunks_before

    def test_reindexes_after_file_change(self, db, project_dir):
        index_project_source(db, project_dir)
        syms_before = db.execute("SELECT count(*) FROM module_members").fetchone()[0]

        new_file = project_dir / "new_module.py"
        new_file.write_text(
            'def brand_new(x: int) -> int:\n'
            '    """A brand new function added later."""\n'
            '    return x * 2\n'
        )

        index_project_source(db, project_dir)
        syms_after = db.execute("SELECT count(*) FROM module_members").fetchone()[0]
        assert syms_after > syms_before

    def test_fts_searchable_after_index(self, db, project_dir):
        index_project_source(db, project_dir)
        rebuild_fulltext_index(db)
        rows = db.execute(
            "SELECT * FROM chunks_fts WHERE chunks_fts MATCH ?", ('"compute"',)
        ).fetchall()
        assert isinstance(rows, list)


class TestWriteDep:
    def test_writes_package_record(self, db):
        data = {
            "name": "fastapi", "version": "0.100", "hash": "abc123",
            "summary": "A web framework",
            "homepage": "https://fastapi.tiangolo.com",
            "requires": '["starlette", "pydantic"]',
            "chunks": [("fastapi", "Overview", "FastAPI is a modern web framework.", "readme")],
            "symbols": [("fastapi", "fastapi", "FastAPI", "class", "()", "", "[]", "Main app class.")],
        }
        _persist_dependency(db, data)
        pkg = db.execute("SELECT * FROM packages WHERE name='fastapi'").fetchone()
        assert pkg is not None
        assert pkg["version"] == "0.100"

    def test_writes_chunks(self, db):
        data = {
            "name": "mypkg", "version": "1.0", "hash": "h",
            "summary": "", "homepage": "", "requires": "[]",
            "chunks": [
                ("mypkg", "Heading", "Body text content here.", "doc"),
                ("mypkg", "API", "API documentation content.", "readme"),
            ],
            "symbols": [],
        }
        _persist_dependency(db, data)
        count = db.execute("SELECT count(*) FROM chunks WHERE package='mypkg'").fetchone()[0]
        assert count == 2

    def test_writes_symbols(self, db):
        data = {
            "name": "mypkg", "version": "1.0", "hash": "h",
            "summary": "", "homepage": "", "requires": "[]",
            "chunks": [],
            "symbols": [("mypkg", "mypkg.core", "run", "def", "()", "None", "[]", "Run it.")],
        }
        _persist_dependency(db, data)
        sym = db.execute("SELECT * FROM module_members WHERE name='run'").fetchone()
        assert sym is not None
        assert sym["kind"] == "def"

    def test_clears_old_data_before_write(self, db):
        data = {
            "name": "mypkg", "version": "1.0", "hash": "h1",
            "summary": "old", "homepage": "", "requires": "[]",
            "chunks": [], "symbols": [],
        }
        _persist_dependency(db, data)

        data["version"] = "2.0"
        data["summary"] = "new"
        data["hash"] = "h2"
        _persist_dependency(db, data)

        pkgs = db.execute("SELECT * FROM packages WHERE name='mypkg'").fetchall()
        assert len(pkgs) == 1
        assert pkgs[0]["version"] == "2.0"


class TestSitePackagesRoot:
    def test_finds_site_packages(self):
        path = "/usr/lib/python3.11/site-packages/requests/api.py"
        assert find_site_packages_root(path).endswith("site-packages")

    def test_finds_dist_packages(self):
        path = "/usr/lib/python3/dist-packages/apt/package.py"
        assert find_site_packages_root(path).endswith("dist-packages")

    def test_fallback_for_unknown_layout(self, tmp_path):
        fake = tmp_path / "some" / "path" / "module.py"
        fake.parent.mkdir(parents=True)
        fake.touch()
        root = find_site_packages_root(str(fake))
        assert root == str(tmp_path / "some")
