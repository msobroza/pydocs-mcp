"""Tests for pure Python fallback implementations (_fallback.py)."""

import os

from pydocs_mcp._fallback import (
    extract_module_doc,
    hash_files,
    parse_py_file,
    read_file,
    read_files_parallel,
    walk_py_files,
)


# ── parse_py_file ────────────────────────────────────────────────────────


class TestParsePyFile:
    def test_finds_function(self):
        src = 'def greet(name: str) -> str:\n    """Say hello."""\n    return f"Hi {name}"\n'
        syms = parse_py_file(src)
        assert any(s.name == "greet" for s in syms)

    def test_finds_class(self):
        src = (
            'class Foo(Base):\n    """A foo class with enough doc to be useful here."""\n    pass\n'
        )
        syms = parse_py_file(src)
        assert any(s.name == "Foo" for s in syms)

    def test_finds_paren_less_class(self):
        # Idiomatic modern-Python class with no base-class parens (`class Config:`)
        # — regression for a regex that required the `(...)` group, silently
        # dropping every base-less class from the static (--no-inspect) index.
        src = 'class Config:\n    """Settings."""\n    pass\n'
        syms = parse_py_file(src)
        assert any(s.name == "Config" and s.kind == "class" for s in syms)

    def test_skips_private(self):
        src = 'def _private(x):\n    """Hidden."""\n    pass\n'
        assert not parse_py_file(src)

    def test_extracts_docstring(self):
        src = 'def foo(x):\n    """Does foo stuff."""\n    pass\n'
        syms = parse_py_file(src)
        assert syms[0].docstring == "Does foo stuff."

    def test_async_def(self):
        src = 'async def fetch(url: str):\n    """Fetch URL asynchronously."""\n    pass\n'
        syms = parse_py_file(src)
        assert syms and syms[0].name == "fetch"

    def test_no_false_docstring_attribution(self):
        src = 'def no_doc(x):\n    pass\n\ndef has_doc(y):\n    """Real doc."""\n    pass\n'
        syms = parse_py_file(src)
        by_name = {s.name: s for s in syms}
        assert by_name["no_doc"].docstring == ""
        assert by_name["has_doc"].docstring == "Real doc."

    def test_empty_source(self):
        assert parse_py_file("") == []


# ── extract_module_doc ───────────────────────────────────────────────────


class TestExtractModuleDoc:
    def test_triple_double_quotes(self):
        src = '"""This is the module docstring."""\n\nimport os\n'
        assert extract_module_doc(src) == "This is the module docstring."

    def test_triple_single_quotes(self):
        src = "'''Single-quoted module doc.'''\n\nimport os\n"
        assert extract_module_doc(src) == "Single-quoted module doc."

    def test_multiline_docstring(self):
        src = '"""First line.\n\nMore details here.\n"""\n'
        doc = extract_module_doc(src)
        assert "First line." in doc
        assert "More details" in doc

    def test_no_docstring(self):
        src = "import os\n\ndef foo():\n    pass\n"
        assert extract_module_doc(src) == ""

    def test_empty_source(self):
        assert extract_module_doc("") == ""

    def test_leading_whitespace_ignored(self):
        src = '\n\n"""Docstring after blank lines."""\n'
        assert extract_module_doc(src) == "Docstring after blank lines."

    def test_truncation_at_5000(self):
        long_doc = '"""' + "x" * 6000 + '"""\n'
        doc = extract_module_doc(long_doc)
        assert len(doc) <= 5000

    def test_comment_before_docstring_blocks(self):
        src = '# comment\n"""Not a module doc."""\n'
        assert extract_module_doc(src) == ""


# ── walk_py_files ────────────────────────────────────────────────────────


class TestWalkPyFiles:
    def test_finds_py_files(self, tmp_path):
        (tmp_path / "a.py").touch()
        (tmp_path / "b.py").touch()
        (tmp_path / "c.txt").touch()
        result = walk_py_files(str(tmp_path))
        assert len(result) == 2
        assert all(f.endswith(".py") for f in result)

    def test_sorted_output(self, tmp_path):
        (tmp_path / "z.py").touch()
        (tmp_path / "a.py").touch()
        (tmp_path / "m.py").touch()
        result = walk_py_files(str(tmp_path))
        assert result == sorted(result)

    def test_recursive(self, tmp_path):
        sub = tmp_path / "pkg" / "sub"
        sub.mkdir(parents=True)
        (tmp_path / "top.py").touch()
        (sub / "deep.py").touch()
        result = walk_py_files(str(tmp_path))
        assert len(result) == 2

    def test_skips_venv(self, tmp_path):
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "hidden.py").touch()
        (tmp_path / "visible.py").touch()
        result = walk_py_files(str(tmp_path))
        assert len(result) == 1

    def test_skips_pycache(self, tmp_path):
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "cached.py").touch()
        (tmp_path / "main.py").touch()
        result = walk_py_files(str(tmp_path))
        assert len(result) == 1

    def test_skips_git(self, tmp_path):
        git = tmp_path / ".git" / "hooks"
        git.mkdir(parents=True)
        (git / "pre-commit.py").touch()
        (tmp_path / "app.py").touch()
        result = walk_py_files(str(tmp_path))
        assert len(result) == 1

    def test_skips_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "script.py").touch()
        (tmp_path / "app.py").touch()
        result = walk_py_files(str(tmp_path))
        assert len(result) == 1

    def test_empty_directory(self, tmp_path):
        assert walk_py_files(str(tmp_path)) == []


# ── hash_files ───────────────────────────────────────────────────────────


class TestHashFiles:
    def test_same_files_same_hash(self, tmp_path):
        f = tmp_path / "file.py"
        f.write_text("content")
        assert hash_files([str(f)]) == hash_files([str(f)])

    def test_different_files_different_hash(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("aaa")
        f2.write_text("bbb")
        assert hash_files([str(f1)]) != hash_files([str(f2)])

    def test_empty_list(self):
        h = hash_files([])
        assert isinstance(h, str) and len(h) > 0

    def test_missing_file_does_not_crash(self, tmp_path):
        h = hash_files([str(tmp_path / "nonexistent.py")])
        assert isinstance(h, str)

    def test_returns_hex_string(self, tmp_path):
        f = tmp_path / "file.py"
        f.write_text("content")
        h = hash_files([str(f)])
        assert len(h) == 16
        int(h, 16)  # valid hex

    def test_mtime_change_changes_hash(self, tmp_path):
        # This is hash_files' entire purpose: package-level cache invalidation
        # (ContentHashStage) relies on the digest moving when a file is touched
        # without its content changing size. A regression that drops mtime from
        # the digest would silently skip re-indexing modified packages.
        f = tmp_path / "file.py"
        f.write_text("content")
        before = hash_files([str(f)])

        current = f.stat().st_mtime
        os.utime(f, (current + 5, current + 5))

        after = hash_files([str(f)])
        assert before != after

    def test_path_mtime_boundary_is_unambiguous(self, tmp_path):
        # Fallback-only edge case: the digest must not be constructible by
        # concatenating two different path lists' bytes into the same stream.
        # Without a separator between path and mtime (and between entries),
        # ['a', '1234'] (two missing paths) and ['a1234'] (one missing path)
        # would hash identically since both contribute just b"a" + b"1234"
        # with no mtime bytes appended (neither path exists on disk).
        assert hash_files(["a", "1234"]) != hash_files(["a1234"])


# ── read_file ────────────────────────────────────────────────────────────


class TestReadFile:
    def test_reads_content(self, tmp_path):
        f = tmp_path / "hello.py"
        f.write_text("print('hello')")
        assert read_file(str(f)) == "print('hello')"

    def test_missing_file_returns_empty(self):
        assert read_file("/nonexistent/path/file.py") == ""

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.touch()
        assert read_file(str(f)) == ""

    def test_unicode_content(self, tmp_path):
        f = tmp_path / "unicode.py"
        f.write_text("# \u65e5\u672c\u8a9e\u30c6\u30b9\u30c8", encoding="utf-8")
        assert "\u65e5\u672c\u8a9e" in read_file(str(f))

    def test_invalid_utf8_byte_returns_lossy_non_empty_content(self, tmp_path):
        # Regression: a single invalid UTF-8 byte (e.g. a latin-1 "caf\xe9"
        # comment, common in older PyPI packages) must NOT vanish the whole
        # file. The fallback uses errors="replace" so the invalid byte
        # becomes U+FFFD rather than the whole file reading as "" \u2014
        # matching the documented substitution contract that the fallback
        # is a drop-in for the native reader on the SAME inputs (see
        # CLAUDE.md "Fallback contract"; src/lib.rs uses
        # String::from_utf8_lossy, which replaces the same way).
        f = tmp_path / "bad_encoding.py"
        f.write_bytes(b"x = 1  # caf\xe9\n")
        content = read_file(str(f))
        assert content == "x = 1  # caf\ufffd\n"


# ── read_files_parallel ──────────────────────────────────────────────────


class TestReadFilesParallel:
    def test_reads_multiple_files(self, tmp_path):
        files = []
        for i in range(5):
            f = tmp_path / f"file{i}.py"
            f.write_text(f"content_{i}")
            files.append(str(f))
        results = read_files_parallel(files)
        assert len(results) == 5

    def test_missing_file_returns_empty_content(self, tmp_path):
        f1 = tmp_path / "exists.py"
        f1.write_text("hello")
        results = read_files_parallel([str(f1), str(tmp_path / "missing.py")])
        by_path = dict(results)
        assert by_path[str(f1)] == "hello"
        assert by_path[str(tmp_path / "missing.py")] == ""

    def test_empty_list(self):
        assert read_files_parallel([]) == []
