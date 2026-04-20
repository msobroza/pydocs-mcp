"""Tests for indexing logic (indexer.py)."""
import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import (
    build_connection_provider,
    open_index_database,
    rebuild_fulltext_index,
)
from pydocs_mcp.indexer import (
    _extract_from_source_files,
    _persist_dependency,
    find_site_packages_root,
    index_project_source,
)
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
)
from pydocs_mcp.storage.sqlite import (
    SqliteChunkRepository,
    SqliteModuleMemberRepository,
    SqlitePackageRepository,
    SqliteUnitOfWork,
)


def _indexing_service(db_path: Path) -> IndexingService:
    provider = build_connection_provider(db_path)
    return IndexingService(
        package_store=SqlitePackageRepository(provider=provider),
        chunk_store=SqliteChunkRepository(provider=provider),
        module_member_store=SqliteModuleMemberRepository(provider=provider),
        unit_of_work=SqliteUnitOfWork(provider=provider),
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
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = open_index_database(path)
    conn.close()
    return path


class TestParseSourceFiles:
    def test_extracts_chunks_and_symbols(self, project_dir):
        py_files = sorted(str(p) for p in project_dir.rglob("*.py"))
        chunks, syms = _extract_from_source_files("mypkg", py_files, str(project_dir))
        assert len(chunks) > 0
        assert len(syms) > 0

    def test_symbol_names(self, project_dir):
        py_files = sorted(str(p) for p in project_dir.rglob("*.py"))
        _, syms = _extract_from_source_files("mypkg", py_files, str(project_dir))
        names = {s.metadata[ModuleMemberFilterField.NAME.value] for s in syms}
        assert "compute" in names
        assert "Engine" in names
        assert "helper" in names

    def test_skips_private_symbols(self, project_dir):
        py_files = sorted(str(p) for p in project_dir.rglob("*.py"))
        _, syms = _extract_from_source_files("mypkg", py_files, str(project_dir))
        names = {s.metadata[ModuleMemberFilterField.NAME.value] for s in syms}
        assert "_private" not in names

    def test_extracts_module_docstrings(self, project_dir):
        py_files = sorted(str(p) for p in project_dir.rglob("*.py"))
        chunks, _ = _extract_from_source_files("mypkg", py_files, str(project_dir))
        doc_chunks = [
            c for c in chunks
            if c.metadata.get(ChunkFilterField.ORIGIN.value) == "project_doc"
        ]
        assert len(doc_chunks) > 0

    def test_empty_file_list(self):
        chunks, syms = _extract_from_source_files("mypkg", [], ".")
        assert chunks == []
        assert syms == []

    def test_kind_prefix(self, project_dir):
        py_files = sorted(str(p) for p in project_dir.rglob("*.py"))
        chunks, _ = _extract_from_source_files("mypkg", py_files, str(project_dir), "dep")
        kinds = {c.metadata.get(ChunkFilterField.ORIGIN.value) for c in chunks}
        assert all(k.startswith("dep") for k in kinds)


class TestIndexProject:
    def test_indexes_project_files(self, db_path, project_dir):
        index_project_source(_indexing_service(db_path), project_dir)
        conn = open_index_database(db_path)
        pkgs = conn.execute("SELECT * FROM packages WHERE name='__project__'").fetchone()
        conn.close()
        assert pkgs is not None
        assert pkgs["version"] == "local"

    def test_creates_chunks(self, db_path, project_dir):
        index_project_source(_indexing_service(db_path), project_dir)
        conn = open_index_database(db_path)
        count = conn.execute(
            "SELECT count(*) FROM chunks WHERE package='__project__'"
        ).fetchone()[0]
        conn.close()
        assert count > 0

    def test_creates_symbols(self, db_path, project_dir):
        index_project_source(_indexing_service(db_path), project_dir)
        conn = open_index_database(db_path)
        count = conn.execute(
            "SELECT count(*) FROM module_members WHERE package='__project__'"
        ).fetchone()[0]
        conn.close()
        assert count > 0

    def test_caching_skips_unchanged(self, db_path, project_dir):
        service = _indexing_service(db_path)
        index_project_source(service, project_dir)
        conn = open_index_database(db_path)
        chunks_before = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        conn.close()

        index_project_source(service, project_dir)
        conn = open_index_database(db_path)
        chunks_after = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        conn.close()
        assert chunks_after == chunks_before

    def test_reindexes_after_file_change(self, db_path, project_dir):
        service = _indexing_service(db_path)
        index_project_source(service, project_dir)
        conn = open_index_database(db_path)
        syms_before = conn.execute("SELECT count(*) FROM module_members").fetchone()[0]
        conn.close()

        new_file = project_dir / "new_module.py"
        new_file.write_text(
            'def brand_new(x: int) -> int:\n'
            '    """A brand new function added later."""\n'
            '    return x * 2\n'
        )

        index_project_source(service, project_dir)
        conn = open_index_database(db_path)
        syms_after = conn.execute("SELECT count(*) FROM module_members").fetchone()[0]
        conn.close()
        assert syms_after > syms_before

    def test_fts_searchable_after_index(self, db_path, project_dir):
        index_project_source(_indexing_service(db_path), project_dir)
        conn = open_index_database(db_path)
        rebuild_fulltext_index(conn)
        rows = conn.execute(
            "SELECT * FROM chunks_fts WHERE chunks_fts MATCH ?", ('"compute"',)
        ).fetchall()
        conn.close()
        assert isinstance(rows, list)


def _make_chunk(package: str, title: str, text: str, origin: str) -> Chunk:
    return Chunk(
        text=text,
        metadata={
            ChunkFilterField.PACKAGE.value: package,
            ChunkFilterField.TITLE.value: title,
            ChunkFilterField.ORIGIN.value: origin,
        },
    )


def _make_member(
    package: str, module: str, name: str, kind: str, signature: str,
    return_annotation: str, docstring: str,
) -> ModuleMember:
    return ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: package,
            ModuleMemberFilterField.MODULE.value: module,
            ModuleMemberFilterField.NAME.value: name,
            ModuleMemberFilterField.KIND.value: kind,
            "signature": signature,
            "return_annotation": return_annotation,
            "parameters": (),
            "docstring": docstring,
        }
    )


class TestWriteDep:
    def test_writes_package_record(self, db_path):
        data = {
            "name": "fastapi", "version": "0.100", "hash": "abc123",
            "summary": "A web framework",
            "homepage": "https://fastapi.tiangolo.com",
            "requires": ("starlette", "pydantic"),
            "chunks": [_make_chunk("fastapi", "Overview", "FastAPI is a modern web framework.", "readme")],
            "symbols": [_make_member("fastapi", "fastapi", "FastAPI", "class", "()", "", "Main app class.")],
        }
        asyncio.run(_persist_dependency(_indexing_service(db_path), data))
        conn = open_index_database(db_path)
        pkg = conn.execute("SELECT * FROM packages WHERE name='fastapi'").fetchone()
        conn.close()
        assert pkg is not None
        assert pkg["version"] == "0.100"

    def test_writes_chunks(self, db_path):
        data = {
            "name": "mypkg", "version": "1.0", "hash": "h",
            "summary": "", "homepage": "", "requires": (),
            "chunks": [
                _make_chunk("mypkg", "Heading", "Body text content here.", "doc"),
                _make_chunk("mypkg", "API", "API documentation content.", "readme"),
            ],
            "symbols": [],
        }
        asyncio.run(_persist_dependency(_indexing_service(db_path), data))
        conn = open_index_database(db_path)
        count = conn.execute("SELECT count(*) FROM chunks WHERE package='mypkg'").fetchone()[0]
        conn.close()
        assert count == 2

    def test_writes_symbols(self, db_path):
        data = {
            "name": "mypkg", "version": "1.0", "hash": "h",
            "summary": "", "homepage": "", "requires": (),
            "chunks": [],
            "symbols": [_make_member("mypkg", "mypkg.core", "run", "def", "()", "None", "Run it.")],
        }
        asyncio.run(_persist_dependency(_indexing_service(db_path), data))
        conn = open_index_database(db_path)
        sym = conn.execute("SELECT * FROM module_members WHERE name='run'").fetchone()
        conn.close()
        assert sym is not None
        assert sym["kind"] == "def"

    def test_clears_old_data_before_write(self, db_path):
        service = _indexing_service(db_path)
        data = {
            "name": "mypkg", "version": "1.0", "hash": "h1",
            "summary": "old", "homepage": "", "requires": (),
            "chunks": [], "symbols": [],
        }
        asyncio.run(_persist_dependency(service, data))

        data["version"] = "2.0"
        data["summary"] = "new"
        data["hash"] = "h2"
        asyncio.run(_persist_dependency(service, data))

        conn = open_index_database(db_path)
        pkgs = conn.execute("SELECT * FROM packages WHERE name='mypkg'").fetchall()
        conn.close()
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
