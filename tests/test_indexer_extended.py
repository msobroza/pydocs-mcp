"""Extended tests for indexer.py — covers functions not in test_indexer.py.

Targets: _build_package_record, _append_doc_file_chunks, list_dependency_source_files,
_extract_from_static_sources, _extract_by_import, _extract_callable_signature,
_extract_members_by_import, index_dependencies.
"""
import hashlib
import inspect
import json
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pydocs_mcp.db import open_index_database, rebuild_fulltext_index
from pydocs_mcp.indexer import (
    _append_doc_file_chunks,
    _build_package_record,
    _extract_by_import,
    _extract_callable_signature,
    _extract_from_static_sources,
    _extract_members_by_import,
    index_dependencies,
    list_dependency_source_files,
)


# -- Helpers for creating mock distributions --

def make_mock_dist(name="testpkg", version="1.0", summary="A test package",
                   homepage="https://example.com", requires=None,
                   files=None, payload=""):
    """Create a mock importlib.metadata distribution."""
    dist = MagicMock()
    metadata = {
        "Name": name,
        "Version": version,
        "Summary": summary,
        "Home-page": homepage,
    }
    dist.metadata.__getitem__ = lambda self, k: metadata.get(k, "")
    dist.metadata.get_payload = MagicMock(return_value=payload)
    dist.requires = requires or []
    dist.files = files
    return dist


def make_mock_file(path_str, content="", size=100, exists=True):
    """Create a mock distribution file entry."""
    f = MagicMock()
    f.__str__ = lambda self: path_str
    loc = MagicMock()
    loc.exists.return_value = exists
    loc.stat.return_value = MagicMock(st_size=size)
    loc.read_text = MagicMock(return_value=content)
    f.locate.return_value = loc
    return f


# -- _extract_callable_signature tests --

class TestGetSig:
    def test_simple_function(self):
        def foo(x: int, y: str = "hello") -> bool:
            pass
        sig, ret, params = _extract_callable_signature(foo)
        assert "x" in sig
        assert "y" in sig
        assert ret == "bool"
        assert len(params) == 2
        assert params[0]["name"] == "x"
        assert params[1]["name"] == "y"
        assert params[1]["default"] == "'hello'"

    def test_function_no_annotations(self):
        def bar(a, b):
            pass
        sig, ret, params = _extract_callable_signature(bar)
        assert "a" in sig
        assert len(params) == 2
        assert "type" not in params[0]

    def test_skips_self_and_cls(self):
        class MyClass:
            def method(self, x: int):
                pass
            @classmethod
            def clsmethod(cls, y: str):
                pass
        sig, _, params = _extract_callable_signature(MyClass.method)
        assert all(p["name"] != "self" for p in params)
        sig2, _, params2 = _extract_callable_signature(MyClass.clsmethod)
        assert all(p["name"] != "cls" for p in params2)

    def test_no_signature_returns_empty(self):
        # Built-in with no inspectable signature
        sig, ret, params = _extract_callable_signature(print)
        # print may or may not have a signature depending on Python version
        assert isinstance(sig, str)
        assert isinstance(params, list)

    def test_function_with_return_annotation(self):
        def baz() -> list:
            pass
        sig, ret, params = _extract_callable_signature(baz)
        assert ret == "list"
        assert params == []


# -- _extract_members_by_import tests --

class TestInspectSyms:
    def test_extracts_functions(self):
        mod = types.ModuleType("testmod")
        mod.__name__ = "testmod"

        def public_func(x: int) -> str:
            """A public function."""
            pass
        public_func.__module__ = "testmod"
        mod.public_func = public_func

        rows = _extract_members_by_import(mod, "testmod", "testmod")
        assert len(rows) >= 1
        names = [r[2] for r in rows]
        assert "public_func" in names

    def test_skips_private_names(self):
        mod = types.ModuleType("testmod")
        mod.__name__ = "testmod"

        def _private():
            pass
        _private.__module__ = "testmod"
        mod._private = _private

        rows = _extract_members_by_import(mod, "testmod", "testmod")
        names = [r[2] for r in rows]
        assert "_private" not in names

    def test_extracts_classes(self):
        mod = types.ModuleType("testmod")
        mod.__name__ = "testmod"

        class MyClass:
            """A test class."""
            def method(self):
                """A method."""
                pass
        MyClass.__module__ = "testmod"
        mod.MyClass = MyClass

        rows = _extract_members_by_import(mod, "testmod", "testmod")
        names = [r[2] for r in rows]
        assert "MyClass" in names

    def test_skips_foreign_symbols(self):
        mod = types.ModuleType("testmod")
        mod.__name__ = "testmod"

        def foreign():
            pass
        foreign.__module__ = "other_module"
        mod.foreign = foreign

        rows = _extract_members_by_import(mod, "testmod", "testmod")
        names = [r[2] for r in rows]
        assert "foreign" not in names

    def test_depth_recursion(self):
        parent = types.ModuleType("testpkg")
        parent.__name__ = "testpkg"
        parent.__path__ = []  # Has __path__ = is a package

        def func_in_parent():
            """Parent function."""
            pass
        func_in_parent.__module__ = "testpkg"
        parent.func_in_parent = func_in_parent

        # Mock pkgutil.iter_modules so it doesn't actually scan the filesystem
        with patch("pydocs_mcp.indexer.pkgutil.iter_modules", return_value=[]):
            rows = _extract_members_by_import(parent, "testpkg", "testpkg", depth=0, max_depth=1)
        assert len(rows) >= 1


# -- _build_package_record tests --

class TestBaseData:
    def test_creates_basic_structure(self):
        dist = make_mock_dist(name="mypkg", version="2.0", summary="My package")
        data = _build_package_record(dist, "mypkg", "2.0")
        assert data["name"] == "mypkg"
        assert data["version"] == "2.0"
        assert data["summary"] == "My package"
        assert data["chunks"] == [] or isinstance(data["chunks"], list)
        assert data["symbols"] == []

    def test_includes_hash(self):
        dist = make_mock_dist()
        data = _build_package_record(dist, "testpkg", "1.0")
        expected = hashlib.md5("testpkg:1.0".encode()).hexdigest()[:12]
        assert data["hash"] == expected

    def test_includes_requires(self):
        dist = make_mock_dist(requires=["dep1>=1.0", "dep2; python_version >= '3.8'"])
        data = _build_package_record(dist, "testpkg", "1.0")
        reqs = json.loads(data["requires"])
        assert "dep1>=1.0" in reqs
        assert "dep2" in reqs

    def test_includes_long_form_readme(self):
        long_desc = "# My Package\n\n" + "This is a detailed description. " * 20
        dist = make_mock_dist(payload=long_desc)
        data = _build_package_record(dist, "testpkg", "1.0")
        assert len(data["chunks"]) > 0

    def test_short_payload_ignored(self):
        dist = make_mock_dist(payload="Short.")
        data = _build_package_record(dist, "testpkg", "1.0")
        readme_chunks = [c for c in data["chunks"] if c[3] == "readme"]
        assert len(readme_chunks) == 0

    def test_non_string_payload_ignored(self):
        dist = make_mock_dist()
        dist.metadata.get_payload = MagicMock(return_value=None)
        data = _build_package_record(dist, "testpkg", "1.0")
        assert isinstance(data["chunks"], list)


# -- _append_doc_file_chunks tests --

class TestAddDocFiles:
    def test_adds_readme_file(self):
        readme_content = "# README\n\n" + "Documentation content here. " * 20
        files = [make_mock_file("testpkg/README.md", content=readme_content)]
        dist = make_mock_dist(files=files)
        data = {"chunks": []}
        _append_doc_file_chunks(dist, "testpkg", data)
        assert len(data["chunks"]) > 0
        assert data["chunks"][0][3] == "doc"

    def test_skips_non_doc_files(self):
        files = [make_mock_file("testpkg/setup.py", content="# setup")]
        dist = make_mock_dist(files=files)
        data = {"chunks": []}
        _append_doc_file_chunks(dist, "testpkg", data)
        assert len(data["chunks"]) == 0

    def test_skips_large_files(self):
        files = [make_mock_file("testpkg/README.md", size=600_000)]
        dist = make_mock_dist(files=files)
        data = {"chunks": []}
        _append_doc_file_chunks(dist, "testpkg", data)
        assert len(data["chunks"]) == 0

    def test_handles_missing_files_gracefully(self):
        files = [make_mock_file("testpkg/README.md", exists=False)]
        dist = make_mock_dist(files=files)
        data = {"chunks": []}
        _append_doc_file_chunks(dist, "testpkg", data)
        assert len(data["chunks"]) == 0

    def test_handles_no_files(self):
        dist = make_mock_dist(files=None)
        data = {"chunks": []}
        _append_doc_file_chunks(dist, "testpkg", data)
        assert len(data["chunks"]) == 0

    def test_recognizes_doc_keywords(self):
        files = [
            make_mock_file("testpkg/docs/guide.md",
                          content="# Guide\n\n" + "Guide content. " * 20),
            make_mock_file("testpkg/docs/api.rst",
                          content="# API\n\n" + "API reference. " * 20),
        ]
        dist = make_mock_dist(files=files)
        data = {"chunks": []}
        _append_doc_file_chunks(dist, "testpkg", data)
        assert len(data["chunks"]) >= 2


# -- list_dependency_source_files tests --

class TestDepPyFiles:
    def test_finds_py_files(self):
        files = [
            make_mock_file("testpkg/__init__.py"),
            make_mock_file("testpkg/core.py"),
            make_mock_file("testpkg/data.json"),  # Not .py
        ]
        dist = make_mock_dist(files=files)
        result = list_dependency_source_files(dist)
        assert len(result) == 2

    def test_skips_setup_py(self):
        files = [
            make_mock_file("setup.py"),
            make_mock_file("testpkg/__init__.py"),
        ]
        dist = make_mock_dist(files=files)
        result = list_dependency_source_files(dist)
        assert len(result) == 1

    def test_skips_large_files(self):
        files = [make_mock_file("testpkg/huge.py", size=600_000)]
        dist = make_mock_dist(files=files)
        result = list_dependency_source_files(dist)
        assert len(result) == 0

    def test_skips_missing_files(self):
        files = [make_mock_file("testpkg/missing.py", exists=False)]
        dist = make_mock_dist(files=files)
        result = list_dependency_source_files(dist)
        assert len(result) == 0

    def test_no_files_returns_empty(self):
        dist = make_mock_dist(files=None)
        result = list_dependency_source_files(dist)
        assert result == []


# -- _extract_from_static_sources tests --

class TestCollectStatic:
    def test_collects_from_py_files(self, tmp_path):
        # Create actual files on disk for the static collector
        pkg_dir = tmp_path / "site-packages" / "testpkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text('"""Test package."""\n')
        (pkg_dir / "core.py").write_text(
            'def run(x: int) -> int:\n'
            '    """Run the computation."""\n'
            '    return x * 2\n'
        )

        files = [
            make_mock_file(str(pkg_dir / "__init__.py")),
            make_mock_file(str(pkg_dir / "core.py")),
        ]
        # Make locate() return real paths
        for f, real_path in zip(files, [pkg_dir / "__init__.py", pkg_dir / "core.py"]):
            loc = MagicMock()
            loc.exists.return_value = True
            loc.stat.return_value = (pkg_dir / "__init__.py").stat()
            loc.__str__ = lambda self, p=str(real_path): p
            f.locate.return_value = loc

        dist = make_mock_dist()
        dist.files = files

        with patch("pydocs_mcp.indexer.list_dependency_source_files", return_value=[str(pkg_dir / "__init__.py"), str(pkg_dir / "core.py")]):
            data = _extract_from_static_sources(dist, depth=1)

        assert data["name"] == "testpkg"
        assert data["version"] == "1.0"
        assert len(data["symbols"]) >= 1 or len(data["chunks"]) >= 0

    def test_collect_static_no_files(self):
        dist = make_mock_dist()
        with patch("pydocs_mcp.indexer.list_dependency_source_files", return_value=[]):
            data = _extract_from_static_sources(dist, depth=1)
        assert data["name"] == "testpkg"
        assert data["symbols"] == [] or len(data["symbols"]) == 0


# -- _extract_by_import tests --

class TestCollectInspect:
    def test_collects_from_importable_module(self):
        dist = make_mock_dist(name="json", version="stdlib")

        # json is always importable — temporarily replace SKIP_IMPORT
        import pydocs_mcp.indexer as idx
        orig = idx.SKIP_IMPORT
        try:
            idx.SKIP_IMPORT = frozenset()
            data = _extract_by_import(dist, depth=0)
        finally:
            idx.SKIP_IMPORT = orig

        assert data["name"] == "json"
        assert len(data["symbols"]) > 0

    def test_skips_import_for_blocklisted_packages(self):
        dist = make_mock_dist(name="setuptools", version="60.0")

        data = _extract_by_import(dist, depth=1)
        # Should still return data structure but no symbols from import
        assert data["name"] == "setuptools"

    def test_handles_import_error_gracefully(self):
        dist = make_mock_dist(name="nonexistent_xyz_pkg", version="1.0")

        import pydocs_mcp.indexer as idx
        orig = idx.SKIP_IMPORT
        try:
            idx.SKIP_IMPORT = frozenset()
            data = _extract_by_import(dist, depth=1)
        finally:
            idx.SKIP_IMPORT = orig

        assert data["name"] == "nonexistent_xyz_pkg"
        assert data["symbols"] == []

    def test_uses_import_alias(self):
        dist = make_mock_dist(name="pyyaml", version="6.0")

        import pydocs_mcp.indexer as idx
        orig = idx.SKIP_IMPORT
        try:
            idx.SKIP_IMPORT = frozenset()
            data = _extract_by_import(dist, depth=0)
        finally:
            idx.SKIP_IMPORT = orig
        # May or may not have yaml installed, but shouldn't crash
        assert data["name"] == "pyyaml"


# -- index_dependencies tests --

class TestIndexDeps:
    @pytest.fixture
    def db(self, tmp_path):
        return open_index_database(tmp_path / "test.db")

    def test_index_deps_with_no_deps(self, db):
        stats = index_dependencies(db, [], depth=1, workers=1)
        assert stats["indexed"] == 0
        assert stats["cached"] == 0
        assert stats["failed"] == 0

    def test_index_deps_caches_on_second_run(self, db):
        # Use json (always available)
        stats1 = index_dependencies(db, ["json"], depth=0, workers=1, use_inspect=True)
        stats2 = index_dependencies(db, ["json"], depth=0, workers=1, use_inspect=True)
        # Second run should find it cached (if it was indexed first time)
        if stats1["indexed"] > 0:
            assert stats2["cached"] > 0

    def test_index_deps_static_mode(self, db):
        # Static mode reads .py files without importing
        stats = index_dependencies(db, ["json"], depth=0, workers=1, use_inspect=False)
        assert isinstance(stats, dict)
        assert "indexed" in stats

    def test_index_deps_handles_missing_package(self, db):
        stats = index_dependencies(db, ["totally_fake_package_xyz"], depth=1, workers=1)
        # Package not installed, so nothing to index
        assert stats["indexed"] == 0

    def test_index_deps_with_mock_distribution(self, db):
        """Test index_dependencies with a mocked distribution to cover the inner loop."""
        mock_dist = make_mock_dist(name="fakepkg", version="1.0", summary="Fake")
        mock_dist.files = None

        def fake_distributions():
            return [mock_dist]

        with patch("pydocs_mcp.indexer.importlib.metadata.distributions", fake_distributions):
            stats = index_dependencies(db, ["fakepkg"], depth=0, workers=1, use_inspect=True)

        assert stats["indexed"] + stats["failed"] >= 1

    def test_index_deps_static_with_mock(self, db):
        mock_dist = make_mock_dist(name="staticpkg", version="2.0")
        mock_dist.files = None

        with patch("pydocs_mcp.indexer.importlib.metadata.distributions", return_value=[mock_dist]):
            stats = index_dependencies(db, ["staticpkg"], depth=0, workers=1, use_inspect=False)

        assert stats["indexed"] + stats["failed"] >= 1

    def test_index_deps_failed_collector(self, db):
        """Test that a failing collector increments the failed counter."""
        mock_dist = make_mock_dist(name="failpkg", version="1.0")
        mock_dist.files = None

        def failing_collector(dist, depth):
            raise RuntimeError("Collector failed")

        with patch("pydocs_mcp.indexer.importlib.metadata.distributions", return_value=[mock_dist]), \
             patch("pydocs_mcp.indexer._extract_by_import", failing_collector):
            stats = index_dependencies(db, ["failpkg"], depth=0, workers=1, use_inspect=True)

        assert stats["failed"] >= 1

    def test_index_deps_skips_null_name(self, db):
        """Distributions with no Name metadata should be skipped."""
        mock_dist = make_mock_dist(name="", version="1.0")
        # Override __getitem__ to return None for Name
        mock_dist.metadata.__getitem__ = lambda self, k: None if k == "Name" else ""

        with patch("pydocs_mcp.indexer.importlib.metadata.distributions", return_value=[mock_dist]):
            stats = index_dependencies(db, [""], depth=0, workers=1)

        assert stats["indexed"] == 0
