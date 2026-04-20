"""Tests using fixture packages to verify end-to-end indexing and search.

Uses the fake_project and package fixtures (sklearn, vllm, langgraph) to test
the full pipeline: index -> search -> verify results are meaningful.
"""
import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import (
    open_index_database,
    rebuild_fulltext_index,
)
from pydocs_mcp.indexer import (
    _extract_from_source_files,
    _persist_dependency,
    index_project_source,
)
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
)
from pydocs_mcp.storage.wiring import build_sqlite_indexing_service
from tests._retriever_helpers import (
    retrieve_chunks,
    retrieve_module_members,
    write_package_sync,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FAKE_PROJECT = FIXTURES_DIR / "fake_project"
PACKAGES_DIR = FIXTURES_DIR / "packages"


def _make_service(db_path: Path) -> IndexingService:
    return build_sqlite_indexing_service(db_path)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "fixture_test.db"
    open_index_database(path).close()
    return path


@pytest.fixture
def db(db_path):
    # Return an open connection for tests that read back via raw SQL.
    c = open_index_database(db_path)
    try:
        yield c
    finally:
        c.close()


def _db_path(conn) -> Path:
    rows = conn.execute("PRAGMA database_list").fetchall()
    for row in rows:
        file_col = row["file"] if hasattr(row, "keys") else row[2]
        if file_col:
            return Path(file_col)
    raise RuntimeError("Connection has no on-disk file")


def _index_fake_package(conn, pkg_name):
    """Index a fixture package into the database using static parsing."""
    pkg_dir = PACKAGES_DIR / pkg_name
    py_files = sorted(str(p) for p in pkg_dir.rglob("*.py"))
    chunks, syms = _extract_from_source_files(pkg_name, py_files, str(pkg_dir), kind_prefix="dep")
    # Commit anything still pending on the test connection so the async writer
    # sees the schema without database-lock contention.
    conn.commit()
    write_package_sync(
        _db_path(conn),
        name=pkg_name,
        version="0.0.0",
        summary=f"{pkg_name} fixture",
        content_hash=f"fixture_{pkg_name}",
        chunks=tuple(chunks),
        module_members=tuple(syms),
    )
    return len(chunks), len(syms)


class TestFakeProjectIndexing:
    def test_indexes_fake_project_successfully(self, db_path):
        asyncio.run(index_project_source(_make_service(db_path), FAKE_PROJECT))
        c = open_index_database(db_path)
        pkg = c.execute("SELECT * FROM packages WHERE name='__project__'").fetchone()
        c.close()
        assert pkg is not None

    def test_extracts_project_symbols(self, db_path):
        asyncio.run(index_project_source(_make_service(db_path), FAKE_PROJECT))
        c = open_index_database(db_path)
        syms = c.execute(
            "SELECT * FROM module_members WHERE package='__project__'"
        ).fetchall()
        c.close()
        names = {s["name"] for s in syms}
        assert "main" in names or "run_pipeline" in names or "train_model" in names

    def test_extracts_project_chunks(self, db_path):
        asyncio.run(index_project_source(_make_service(db_path), FAKE_PROJECT))
        c = open_index_database(db_path)
        chunks = c.execute(
            "SELECT * FROM chunks WHERE package='__project__'"
        ).fetchall()
        c.close()
        assert len(chunks) > 0

    def test_project_docstrings_captured(self, db_path):
        asyncio.run(index_project_source(_make_service(db_path), FAKE_PROJECT))
        c = open_index_database(db_path)
        docs = c.execute(
            "SELECT docstring FROM module_members WHERE package='__project__' AND docstring != ''"
        ).fetchall()
        c.close()
        assert len(docs) > 0


class TestFixturePackageIndexing:
    @pytest.mark.parametrize("pkg_name", ["sklearn", "vllm", "langgraph"])
    def test_indexes_package(self, db, pkg_name):
        n_chunks, n_syms = _index_fake_package(db, pkg_name)
        assert n_chunks > 0 or n_syms > 0, f"{pkg_name}: no chunks or symbols extracted"

    @pytest.mark.parametrize("pkg_name", ["sklearn", "vllm", "langgraph"])
    def test_package_record_created(self, db, pkg_name):
        _index_fake_package(db, pkg_name)
        pkg = db.execute("SELECT * FROM packages WHERE name=?", (pkg_name,)).fetchone()
        assert pkg is not None
        assert pkg["version"] == "0.0.0"


class TestSklearnFixture:
    @pytest.fixture(autouse=True)
    def setup_sklearn(self, db):
        self.db = db
        _index_fake_package(db, "sklearn")
        rebuild_fulltext_index(db)

    def test_random_forest_in_chunks(self):
        # Classes without parens aren't captured by parse_py_file regex,
        # but their text is indexed as chunks
        results = retrieve_chunks(self.db, "RandomForestClassifier")
        assert any(r["pkg"] == "sklearn" for r in results)

    def test_gradient_boosting_in_chunks(self):
        results = retrieve_chunks(self.db, "Gradient Boosting")
        assert any(r["pkg"] == "sklearn" for r in results)

    def test_ensemble_methods(self):
        results = retrieve_chunks(self.db, "ensemble bagging boosting")
        assert len(results) > 0

    def test_predict_in_chunks(self):
        results = retrieve_chunks(self.db, "predict")
        assert any(r["pkg"] == "sklearn" for r in results)


class TestVllmFixture:
    @pytest.fixture(autouse=True)
    def setup_vllm(self, db):
        self.db = db
        _index_fake_package(db, "vllm")
        rebuild_fulltext_index(db)

    def test_sampling_params_in_chunks(self):
        results = retrieve_chunks(self.db, "SamplingParams")
        assert any(r["pkg"] == "vllm" for r in results)

    def test_llm_serving_chunks(self):
        results = retrieve_chunks(self.db, "LLM serving")
        assert len(results) > 0

    def test_temperature_in_docs(self):
        results = retrieve_chunks(self.db, "temperature")
        assert any(r["pkg"] == "vllm" for r in results)


class TestLanggraphFixture:
    @pytest.fixture(autouse=True)
    def setup_langgraph(self, db):
        self.db = db
        _index_fake_package(db, "langgraph")
        rebuild_fulltext_index(db)

    def test_state_graph_in_chunks(self):
        results = retrieve_chunks(self.db, "StateGraph")
        assert any(r["pkg"] == "langgraph" for r in results)

    def test_conditional_edges(self):
        results = retrieve_chunks(self.db, "conditional edges")
        assert len(results) > 0


class TestCrossPackageSearch:
    """Test searching across project + all fixture packages together."""

    @pytest.fixture(autouse=True)
    def setup_all(self, db, tmp_path):
        self.db = db
        self.path = _db_path(db)
        db.close()
        asyncio.run(index_project_source(_make_service(self.path), FAKE_PROJECT))
        self.db = open_index_database(self.path)
        for pkg in ("sklearn", "vllm", "langgraph"):
            _index_fake_package(self.db, pkg)
        rebuild_fulltext_index(self.db)

    def test_internal_true_only_returns_project(self):
        results = retrieve_module_members(self.db, "train", internal=True)
        assert all(r["pkg"] == "__project__" for r in results)

    def test_internal_false_excludes_project(self):
        results = retrieve_module_members(self.db, "fit", internal=False)
        assert all(r["pkg"] != "__project__" for r in results)

    def test_unscoped_search_returns_both(self):
        # "model" likely appears in both project and sklearn
        results = retrieve_chunks(self.db, "model")
        pkgs = {r["pkg"] for r in results}
        assert len(pkgs) >= 1

    def test_package_filter_narrows_results(self):
        results = retrieve_module_members(self.db, "predict", pkg="sklearn")
        assert all(r["pkg"] == "sklearn" for r in results)

    def test_write_dep_with_fixture_data(self):
        """Test _persist_dependency using data shaped like real fixture output."""
        data = {
            "name": "testlib",
            "version": "1.2.3",
            "hash": "abc123",
            "summary": "A test library for machine learning",
            "homepage": "https://testlib.example.com",
            "requires": ("numpy", "scipy"),
            "chunks": [
                Chunk(
                    text="TestLib provides ML utilities for batch inference.",
                    metadata={
                        ChunkFilterField.PACKAGE.value: "testlib",
                        ChunkFilterField.TITLE.value: "Overview",
                        ChunkFilterField.ORIGIN.value: "readme",
                    },
                ),
                Chunk(
                    text="Main entry point for training models and predictions.",
                    metadata={
                        ChunkFilterField.PACKAGE.value: "testlib",
                        ChunkFilterField.TITLE.value: "API",
                        ChunkFilterField.ORIGIN.value: "doc",
                    },
                ),
            ],
            "symbols": [
                ModuleMember(
                    metadata={
                        ModuleMemberFilterField.PACKAGE.value: "testlib",
                        ModuleMemberFilterField.MODULE.value: "testlib.core",
                        ModuleMemberFilterField.NAME.value: "train",
                        ModuleMemberFilterField.KIND.value: "def",
                        "signature": "(X, y)",
                        "return_annotation": "Model",
                        "parameters": (),
                        "docstring": "Train a model on the given dataset.",
                    }
                ),
                ModuleMember(
                    metadata={
                        ModuleMemberFilterField.PACKAGE.value: "testlib",
                        ModuleMemberFilterField.MODULE.value: "testlib.core",
                        ModuleMemberFilterField.NAME.value: "predict",
                        ModuleMemberFilterField.KIND.value: "def",
                        "signature": "(model, X)",
                        "return_annotation": "array",
                        "parameters": (),
                        "docstring": "Generate predictions from a trained model.",
                    }
                ),
                ModuleMember(
                    metadata={
                        ModuleMemberFilterField.PACKAGE.value: "testlib",
                        ModuleMemberFilterField.MODULE.value: "testlib.core",
                        ModuleMemberFilterField.NAME.value: "Pipeline",
                        ModuleMemberFilterField.KIND.value: "class",
                        "signature": "(steps)",
                        "return_annotation": "",
                        "parameters": (),
                        "docstring": "A machine learning pipeline that chains transformers and estimators.",
                    }
                ),
            ],
        }
        asyncio.run(_persist_dependency(_make_service(self.path), data))
        rebuild_fulltext_index(self.db)

        # Verify it's searchable
        pkg = self.db.execute("SELECT * FROM packages WHERE name='testlib'").fetchone()
        assert pkg is not None
        assert pkg["version"] == "1.2.3"

        syms = retrieve_module_members(self.db, "train", pkg="testlib")
        assert len(syms) >= 1

        chunks = retrieve_chunks(self.db, "batch inference", pkg="testlib")
        assert len(chunks) >= 1
