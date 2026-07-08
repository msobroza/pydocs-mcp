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
from pydocs_mcp.constants import DOCSTRING_LOOKAHEAD, MODULE_DOCSTRING_MAX


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

    def test_multibyte_docstring_straddling_lookahead(self):
        # 300 two-byte (é) code points: 300 chars but 600 bytes, so this
        # closes within a code-point-bounded 500-char lookahead but not a
        # byte-bounded 500-byte one. Both sides now use byte-bounded
        # DOCSTRING_LOOKAHEAD (src/lib.rs's safe_truncate, mirrored by
        # _fallback._safe_truncate) so both should identically fail to find
        # the docstring here — this pins that parity (constants.py's "SYNC:"
        # note).
        doc_body = "é" * 300
        src = f'def foo(x):\n    """{doc_body}"""\n    pass\n'
        assert len(doc_body) < DOCSTRING_LOOKAHEAD
        assert len(doc_body.encode("utf-8")) > DOCSTRING_LOOKAHEAD

        rust_syms = [(s.name, s.docstring) for s in rust.parse_py_file(src)]
        py_syms = [(s.name, s.docstring) for s in py.parse_py_file(src)]
        assert rust_syms == py_syms


class TestExtractModuleDocParity:
    def test_basic(self):
        src = '"""Module docstring here."""\n\nimport os\n'
        assert rust.extract_module_doc(src) == py.extract_module_doc(src)

    def test_multibyte_docstring_beyond_max(self):
        # 2-byte code points, well past MODULE_DOCSTRING_MAX in both chars and
        # bytes. Both sides truncate to MODULE_DOCSTRING_MAX *bytes* (Rust's
        # safe_truncate, mirrored by _fallback._safe_truncate), rounded down
        # to a char boundary — for 2-byte text that keeps roughly half as
        # many characters as a naive code-point slice would. A drop-in
        # fallback (per CLAUDE.md's fallback contract) must match exactly.
        doc_body = "é" * (MODULE_DOCSTRING_MAX + 1000)
        src = f'"""{doc_body}"""\n\nimport os\n'
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

    @pytest.mark.skipif(
        os.name == "nt", reason="symlink creation needs elevated privileges on Windows"
    )
    def test_symlinked_py_file(self, tmp_path):
        # Regression: a symlink to a real .py file inside the walked root
        # (common in editable installs / pyenv shims / nix-store layouts).
        # WalkDir (Rust) runs with follow_links disabled and filters on the
        # entry's OWN file_type, so a symlink entry is neither a dir nor a
        # file per file_type().is_file() -> Rust drops it. os.walk (Python)
        # puts symlinks-to-files in `filenames` regardless -> the fallback
        # includes it. Engine switch must not change the discovered file
        # set (different members indexed, different packages.content_hash).
        real = tmp_path / "real.py"
        real.write_text("x = 1\n")
        linked = tmp_path / "linked.py"
        os.symlink(real, linked)

        assert rust.walk_py_files(str(tmp_path)) == py.walk_py_files(str(tmp_path))

    def test_root_is_a_py_file(self, tmp_path):
        # Regression: root = a .py FILE (not a directory) — a caller mistake,
        # e.g. an off-by-one in package resolution. Rust's WalkDir used to
        # yield the root entry itself (passes the dir-only filter_entry,
        # then is_file() + .py extension match), returning [root]. Python's
        # os.walk() yields nothing for a non-directory root, returning [].
        # Both engines now require root to be a directory (see
        # walk_py_files_impl's is_dir() guard in src/lib.rs), matching
        # os.walk() semantics.
        f = tmp_path / "only.py"
        f.touch()
        assert rust.walk_py_files(str(f)) == py.walk_py_files(str(f)) == []

    def test_root_does_not_exist(self, tmp_path):
        missing = tmp_path / "missing"
        assert rust.walk_py_files(str(missing)) == py.walk_py_files(str(missing)) == []


class TestHashFilesParity:
    def test_mtime_change_changes_hash(self, tmp_path):
        # Behavioral (not value-equality) check per engine: hash_files' entire
        # purpose is mtime sensitivity for package-level cache invalidation
        # (ContentHashStage). Verify the Rust engine also moves the digest
        # when a file is touched, mirroring test_fallback.py's fallback check.
        f = tmp_path / "file.py"
        f.write_text("content")
        before = rust.hash_files([str(f)])

        current = f.stat().st_mtime
        os.utime(f, (current + 5, current + 5))

        after = rust.hash_files([str(f)])
        assert before != after


class TestReadFilesParallelParity:
    def test_basic(self, tmp_path):
        files = []
        for i in range(3):
            f = tmp_path / f"f{i}.py"
            f.write_text(f"content_{i}")
            files.append(str(f))
        assert sorted(rust.read_files_parallel(files)) == sorted(py.read_files_parallel(files))

    def test_invalid_utf8_byte(self, tmp_path):
        # Regression: a single invalid UTF-8 byte (e.g. a latin-1 "caf\xe9"
        # comment, common in older PyPI packages) must produce the SAME
        # content on both backends per the documented fallback substitution
        # contract (CLAUDE.md "Fallback contract": "Every Rust function ...
        # must have a matching pure Python implementation ... same signature
        # and behavior"). Before the fix, native fs::read_to_string()
        # .unwrap_or_default() returned "" (silently dropping the whole
        # file from the index) while the Python fallback's errors="ignore"
        # returned lossy-but-non-empty content — a real index divergence
        # between native and pure-Python deployments of the same project.
        f = tmp_path / "bad_encoding.py"
        f.write_bytes(b"x = 1  # caf\xe9\n")
        path = str(f)

        rust_result = dict(rust.read_files_parallel([path]))
        py_result = dict(py.read_files_parallel([path]))

        assert rust_result[path] == py_result[path]
        assert rust_result[path] != ""


class TestReadFileParity:
    def test_invalid_utf8_byte(self, tmp_path):
        # Same as TestReadFilesParallelParity.test_invalid_utf8_byte but
        # through the single-file read_file entry point.
        f = tmp_path / "bad_encoding.py"
        f.write_bytes(b"x = 1  # caf\xe9\n")
        path = str(f)

        assert rust.read_file(path) == py.read_file(path)
        assert rust.read_file(path) != ""
