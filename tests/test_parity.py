"""Parity tests: verify Rust native module and Python fallback produce identical output.

These tests are skipped if the Rust extension is not compiled.
Run with: maturin develop --release && pytest tests/test_parity.py -v
"""
import os
import pytest

try:
    from pydocs_mcp import _native as rust
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False

from pydocs_mcp import _fallback as py


pytestmark = pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust native module not compiled")


class TestParsePyFileParity:
    def test_basic(self):
        src = (
            'def greet(name: str) -> str:\n    """Say hello."""\n    return f"Hi {name}"\n\n'
            'class Engine(object):\n    """Compute engine."""\n    pass\n'
        )
        rust_syms = [(s.name, s.kind, s.signature, s.docstring) for s in rust.parse_py_file(src)]
        py_syms = [(s.name, s.kind, s.signature, s.docstring) for s in py.parse_py_file(src)]
        assert rust_syms == py_syms


class TestExtractModuleDocParity:
    def test_basic(self):
        src = '"""Module docstring here."""\n\nimport os\n'
        assert rust.extract_module_doc(src) == py.extract_module_doc(src)


class TestWalkPyFilesParity:
    def test_sorted_output(self, tmp_path):
        (tmp_path / "z.py").touch()
        (tmp_path / "a.py").touch()
        (tmp_path / "m.py").touch()
        assert rust.walk_py_files(str(tmp_path)) == py.walk_py_files(str(tmp_path))

    def test_skip_dirs(self, tmp_path):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "hidden.py").touch()
        (tmp_path / "visible.py").touch()
        assert rust.walk_py_files(str(tmp_path)) == py.walk_py_files(str(tmp_path))


class TestReadFilesParallelParity:
    def test_basic(self, tmp_path):
        files = []
        for i in range(3):
            f = tmp_path / f"f{i}.py"
            f.write_text(f"content_{i}")
            files.append(str(f))
        assert sorted(rust.read_files_parallel(files)) == sorted(py.read_files_parallel(files))
