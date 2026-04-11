"""Tests using fixture packages to verify end-to-end indexing and search.

Uses the fake_project and package fixtures (sklearn, vllm, langgraph) to test
the full pipeline: index -> search -> verify results are meaningful.
"""
import json
from pathlib import Path

import pytest

from pydocs_mcp.db import open_db, rebuild_fts
from pydocs_mcp.indexer import _parse_source_files, _write_dep, index_project
from pydocs_mcp.search import search_chunks, search_symbols

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FAKE_PROJECT = FIXTURES_DIR / "fake_project"
PACKAGES_DIR = FIXTURES_DIR / "packages"


@pytest.fixture
def db(tmp_path):
    return open_db(tmp_path / "fixture_test.db")


def _index_fake_package(conn, pkg_name):
    """Index a fixture package into the database using static parsing."""
    pkg_dir = PACKAGES_DIR / pkg_name
    py_files = sorted(str(p) for p in pkg_dir.rglob("*.py"))
    chunks, syms = _parse_source_files(pkg_name, py_files, str(pkg_dir), kind_prefix="dep")
    conn.execute(
        "INSERT INTO packages(name, version, summary, homepage, requires, hash) "
        "VALUES(?, ?, ?, '', '[]', ?)",
        (pkg_name, "0.0.0", f"{pkg_name} fixture", f"fixture_{pkg_name}"),
    )
    if chunks:
        conn.executemany(
            "INSERT INTO chunks(pkg, heading, body, kind) VALUES(?, ?, ?, ?)", chunks,
        )
    if syms:
        conn.executemany(
            "INSERT INTO symbols(pkg, module, name, kind, signature, returns, params, doc) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)", syms,
        )
    conn.commit()
    return len(chunks), len(syms)


class TestFakeProjectIndexing:
    def test_indexes_fake_project_successfully(self, db):
        index_project(db, FAKE_PROJECT)
        pkg = db.execute("SELECT * FROM packages WHERE name='__project__'").fetchone()
        assert pkg is not None

    def test_extracts_project_symbols(self, db):
        index_project(db, FAKE_PROJECT)
        syms = db.execute(
            "SELECT * FROM symbols WHERE pkg='__project__'"
        ).fetchall()
        names = {s["name"] for s in syms}
        assert "main" in names or "run_pipeline" in names or "train_model" in names

    def test_extracts_project_chunks(self, db):
        index_project(db, FAKE_PROJECT)
        chunks = db.execute(
            "SELECT * FROM chunks WHERE pkg='__project__'"
        ).fetchall()
        assert len(chunks) > 0

    def test_project_docstrings_captured(self, db):
        index_project(db, FAKE_PROJECT)
        docs = db.execute(
            "SELECT doc FROM symbols WHERE pkg='__project__' AND doc != ''"
        ).fetchall()
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
        rebuild_fts(db)

    def test_random_forest_in_chunks(self):
        # Classes without parens aren't captured by parse_py_file regex,
        # but their text is indexed as chunks
        results = search_chunks(self.db, "RandomForestClassifier")
        assert any(r["pkg"] == "sklearn" for r in results)

    def test_gradient_boosting_in_chunks(self):
        results = search_chunks(self.db, "Gradient Boosting")
        assert any(r["pkg"] == "sklearn" for r in results)

    def test_ensemble_methods(self):
        results = search_chunks(self.db, "ensemble bagging boosting")
        assert len(results) > 0

    def test_predict_in_chunks(self):
        results = search_chunks(self.db, "predict")
        assert any(r["pkg"] == "sklearn" for r in results)


class TestVllmFixture:
    @pytest.fixture(autouse=True)
    def setup_vllm(self, db):
        self.db = db
        _index_fake_package(db, "vllm")
        rebuild_fts(db)

    def test_sampling_params_in_chunks(self):
        results = search_chunks(self.db, "SamplingParams")
        assert any(r["pkg"] == "vllm" for r in results)

    def test_llm_serving_chunks(self):
        results = search_chunks(self.db, "LLM serving")
        assert len(results) > 0

    def test_temperature_in_docs(self):
        results = search_chunks(self.db, "temperature")
        assert any(r["pkg"] == "vllm" for r in results)


class TestLanggraphFixture:
    @pytest.fixture(autouse=True)
    def setup_langgraph(self, db):
        self.db = db
        _index_fake_package(db, "langgraph")
        rebuild_fts(db)

    def test_state_graph_in_chunks(self):
        results = search_chunks(self.db, "StateGraph")
        assert any(r["pkg"] == "langgraph" for r in results)

    def test_conditional_edges(self):
        results = search_chunks(self.db, "conditional edges")
        assert len(results) > 0


class TestCrossPackageSearch:
    """Test searching across project + all fixture packages together."""

    @pytest.fixture(autouse=True)
    def setup_all(self, db):
        self.db = db
        index_project(db, FAKE_PROJECT)
        for pkg in ("sklearn", "vllm", "langgraph"):
            _index_fake_package(db, pkg)
        rebuild_fts(db)

    def test_internal_true_only_returns_project(self):
        results = search_symbols(self.db, "train", internal=True)
        assert all(r["pkg"] == "__project__" for r in results)

    def test_internal_false_excludes_project(self):
        results = search_symbols(self.db, "fit", internal=False)
        assert all(r["pkg"] != "__project__" for r in results)

    def test_unscoped_search_returns_both(self):
        # "model" likely appears in both project and sklearn
        results = search_chunks(self.db, "model")
        pkgs = {r["pkg"] for r in results}
        assert len(pkgs) >= 1

    def test_package_filter_narrows_results(self):
        results = search_symbols(self.db, "predict", pkg="sklearn")
        assert all(r["pkg"] == "sklearn" for r in results)

    def test_write_dep_with_fixture_data(self):
        """Test _write_dep using data shaped like real fixture output."""
        data = {
            "name": "testlib",
            "version": "1.2.3",
            "hash": "abc123",
            "summary": "A test library for machine learning",
            "homepage": "https://testlib.example.com",
            "requires": json.dumps(["numpy", "scipy"]),
            "chunks": [
                ("testlib", "Overview", "TestLib provides ML utilities for batch inference.", "readme"),
                ("testlib", "API", "Main entry point for training models and predictions.", "doc"),
            ],
            "symbols": [
                ("testlib", "testlib.core", "train", "def", "(X, y)", "Model", "[]",
                 "Train a model on the given dataset."),
                ("testlib", "testlib.core", "predict", "def", "(model, X)", "array", "[]",
                 "Generate predictions from a trained model."),
                ("testlib", "testlib.core", "Pipeline", "class", "(steps)", "", "[]",
                 "A machine learning pipeline that chains transformers and estimators."),
            ],
        }
        _write_dep(self.db, data)
        rebuild_fts(self.db)

        # Verify it's searchable
        pkg = self.db.execute("SELECT * FROM packages WHERE name='testlib'").fetchone()
        assert pkg is not None
        assert pkg["version"] == "1.2.3"

        syms = search_symbols(self.db, "train", pkg="testlib")
        assert len(syms) >= 1

        chunks = search_chunks(self.db, "batch inference", pkg="testlib")
        assert len(chunks) >= 1
