"""Unit tests for :class:`AstPythonChunker` (Task 14 — sub-PR #5, spec §8.1).

Covers:
- ``ast.parse`` success → MODULE → FUNCTION / CLASS / IMPORT_BLOCK tree.
- ``SyntaxError`` fallback → single MODULE node with full source text.
- Import grouping into ``IMPORT_BLOCK``.
- Class body split: class direct-text ends at first method's line (spec §4.1.1).
- Module docstring extracted into MODULE ``text`` (direct-text rule).
- Function docstring stored in ``extra_metadata["docstring"]``.
- Fenced code blocks inside docstrings become ``CODE_EXAMPLE`` children.
- Dotted qualified names: ``module.function``, ``module.Class.method``.
- ``content_hash`` deterministic for identical inputs.
- ``from_config(ChunkingConfig())`` returns an instance.
- Decorator registered ``AstPythonChunker`` at import under ``.py``.
- Async-def function titled ``"async def ..."``.
- ``class Foo(Bar, Baz):`` → ``inherits_from == ("Bar", "Baz")``.
- Absolute file path + ``root`` yields dotted module name.
- Fenced code block language tag captured in ``extra_metadata["language"]``.
"""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.chunkers import AstPythonChunker
from pydocs_mcp.extraction.config import ChunkingConfig
from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind
from pydocs_mcp.extraction.serialization import chunker_registry


def _build(content: str, *, path: str = "pkg/mod.py", package: str = "pkg",
           root: Path | None = None) -> DocumentNode:
    """Run the chunker on inline content and return the MODULE root."""
    root = root if root is not None else Path("/tmp/fake_root")
    return AstPythonChunker().build_tree(
        path=str(Path(root) / path) if root is not None else path,
        content=content,
        package=package,
        root=Path(root),
    )


def _find_child(root: DocumentNode, kind: NodeKind, title: str | None = None,
                ) -> DocumentNode | None:
    for c in root.children:
        if c.kind == kind and (title is None or c.title == title):
            return c
    return None


# ── 1. Parse success: simple module + function ─────────────────────────────

def test_parse_success_yields_module_with_function_child(tmp_path: Path) -> None:
    src = '"""Module doc."""\n\ndef foo():\n    """Foo doc."""\n    return 1\n'
    root = _build(src, path="pkg/mod.py", root=tmp_path)
    assert root.kind == NodeKind.MODULE
    func = _find_child(root, NodeKind.FUNCTION)
    assert func is not None
    assert func.title == "def foo()"
    assert func.kind == NodeKind.FUNCTION


# ── 2. SyntaxError fallback → MODULE-only with full source ─────────────────

def test_syntax_error_falls_back_to_full_source_module(tmp_path: Path) -> None:
    src = "def broken(:\n    pass\n"
    root = _build(src, root=tmp_path)
    assert root.kind == NodeKind.MODULE
    assert root.text == src
    assert root.children == ()


# ── 3. Import grouping → IMPORT_BLOCK ──────────────────────────────────────

def test_imports_grouped_into_import_block(tmp_path: Path) -> None:
    src = "import os\nfrom sys import path\n\ndef f():\n    pass\n"
    root = _build(src, root=tmp_path)
    imp = _find_child(root, NodeKind.IMPORT_BLOCK)
    assert imp is not None
    assert "import os" in imp.text
    assert "from sys import path" in imp.text
    # End line covers both imports.
    assert imp.start_line == 1
    assert imp.end_line == 2


# ── 4. Class with methods → CLASS + METHOD children, direct text stops ────

def test_class_with_methods_has_method_children_and_split_text(tmp_path: Path) -> None:
    src = (
        'class Foo:\n'
        '    """Foo doc."""\n'
        '    attr = 1\n'
        '    def bar(self):\n'
        '        return 2\n'
        '    def baz(self):\n'
        '        return 3\n'
    )
    root = _build(src, root=tmp_path)
    cls = _find_child(root, NodeKind.CLASS)
    assert cls is not None
    assert cls.title == "class Foo"
    methods = [c for c in cls.children if c.kind == NodeKind.METHOD]
    assert {m.title for m in methods} == {"def bar()", "def baz()"}
    # Direct text stops before `def bar`.
    assert "def bar" not in cls.text
    assert "def baz" not in cls.text
    # But the class body up to that first method IS included.
    assert "attr = 1" in cls.text


# ── 5. Module docstring → MODULE.text (direct-text rule) ───────────────────

def test_module_docstring_becomes_module_text(tmp_path: Path) -> None:
    src = '"""This is the module docstring."""\n\ndef f():\n    pass\n'
    root = _build(src, root=tmp_path)
    assert root.text == "This is the module docstring."


# ── 6. Function docstring → extra_metadata ─────────────────────────────────

def test_function_docstring_stored_in_extra_metadata(tmp_path: Path) -> None:
    src = 'def f():\n    """Hello doc."""\n    return None\n'
    root = _build(src, root=tmp_path)
    func = _find_child(root, NodeKind.FUNCTION)
    assert func is not None
    assert func.extra_metadata["docstring"] == "Hello doc."
    assert func.summary == "Hello doc."


# ── 7. Fenced code block in module docstring → CODE_EXAMPLE child of MODULE

def test_module_docstring_fenced_block_yields_code_example(tmp_path: Path) -> None:
    src = (
        '"""Module doc.\n'
        '\n'
        '```python\n'
        'x = 1\n'
        '```\n'
        '"""\n'
    )
    root = _build(src, root=tmp_path)
    examples = [c for c in root.children if c.kind == NodeKind.CODE_EXAMPLE]
    assert len(examples) == 1
    assert examples[0].text == "x = 1"
    assert examples[0].extra_metadata["language"] == "python"


# ── 8. Fenced code block in function docstring → CODE_EXAMPLE child of FUNC

def test_function_docstring_fenced_block_yields_code_example(tmp_path: Path) -> None:
    src = (
        'def f():\n'
        '    """Func.\n'
        '\n'
        '    ```python\n'
        '    print(1)\n'
        '    ```\n'
        '    """\n'
        '    return 1\n'
    )
    root = _build(src, root=tmp_path)
    func = _find_child(root, NodeKind.FUNCTION)
    assert func is not None
    examples = [c for c in func.children if c.kind == NodeKind.CODE_EXAMPLE]
    assert len(examples) == 1
    assert "print(1)" in examples[0].text


# ── 9. qualified_name is dotted: module, module.func, module.Class.method ──

def test_qualified_names_are_dotted(tmp_path: Path) -> None:
    src = (
        'def f():\n'
        '    pass\n'
        'class C:\n'
        '    def m(self):\n'
        '        pass\n'
    )
    root = _build(src, path="pkg/mod.py", root=tmp_path)
    assert root.qualified_name == "pkg.mod"
    func = _find_child(root, NodeKind.FUNCTION, "def f()")
    cls = _find_child(root, NodeKind.CLASS)
    assert func is not None and cls is not None
    assert func.qualified_name == "pkg.mod.f"
    method = next(c for c in cls.children if c.kind == NodeKind.METHOD)
    assert method.qualified_name == "pkg.mod.C.m"


# ── 10. content_hash stable across runs for the same input ─────────────────

def test_content_hash_is_deterministic(tmp_path: Path) -> None:
    src = '"""same doc."""\n'
    a = _build(src, path="pkg/mod.py", root=tmp_path)
    b = _build(src, path="pkg/mod.py", root=tmp_path)
    assert a.content_hash == b.content_hash
    assert len(a.content_hash) == 12


# ── 11. from_config(ChunkingConfig()) returns an instance ──────────────────

def test_from_config_returns_instance() -> None:
    inst = AstPythonChunker.from_config(ChunkingConfig())
    assert isinstance(inst, AstPythonChunker)


# ── 12. Decorator registered AstPythonChunker under ".py" at import ────────

def test_decorator_registered_at_import() -> None:
    assert chunker_registry[".py"] is AstPythonChunker


# ── 13. Async-def function → title starts with "async def" ─────────────────

def test_async_function_title_has_async_prefix(tmp_path: Path) -> None:
    src = "async def f():\n    return 1\n"
    root = _build(src, root=tmp_path)
    func = _find_child(root, NodeKind.FUNCTION)
    assert func is not None
    assert func.title == "async def f()"


# ── 14. Class bases → inherits_from tuple ──────────────────────────────────

def test_class_inherits_from_captured(tmp_path: Path) -> None:
    src = "class Foo(Bar, Baz):\n    pass\n"
    root = _build(src, root=tmp_path)
    cls = _find_child(root, NodeKind.CLASS)
    assert cls is not None
    assert cls.extra_metadata["inherits_from"] == ("Bar", "Baz")


# ── 15. Absolute path + root resolves to dotted module name ────────────────

def test_absolute_path_with_root_yields_dotted_module(tmp_path: Path) -> None:
    file_path = tmp_path / "pkg" / "sub" / "mod.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("def f():\n    pass\n", encoding="utf-8")
    root = AstPythonChunker().build_tree(
        path=str(file_path.resolve()),
        content=file_path.read_text(encoding="utf-8"),
        package="pkg",
        root=tmp_path,
    )
    assert root.qualified_name == "pkg.sub.mod"


# ── 16. Fenced code block language tag captured ────────────────────────────

def test_fenced_block_language_tag_captured(tmp_path: Path) -> None:
    src = (
        'def f():\n'
        '    """Doc.\n'
        '\n'
        '    ```bash\n'
        '    echo hi\n'
        '    ```\n'
        '    """\n'
        '    pass\n'
    )
    root = _build(src, root=tmp_path)
    func = _find_child(root, NodeKind.FUNCTION)
    assert func is not None
    ex = next(c for c in func.children if c.kind == NodeKind.CODE_EXAMPLE)
    assert ex.extra_metadata["language"] == "bash"


# ── 17. Bare __init__.py → dotted module = package path (no "__init__") ───

def test_init_py_drops_init_suffix(tmp_path: Path) -> None:
    init_path = tmp_path / "pkg" / "sub" / "__init__.py"
    init_path.parent.mkdir(parents=True)
    init_path.write_text("", encoding="utf-8")
    root = AstPythonChunker().build_tree(
        path=str(init_path.resolve()),
        content="",
        package="pkg",
        root=tmp_path,
    )
    assert root.qualified_name == "pkg.sub"


# ── 18. Empty file is handled gracefully (no children, MODULE only) ────────

def test_empty_file_yields_module_only(tmp_path: Path) -> None:
    root = _build("", root=tmp_path)
    assert root.kind == NodeKind.MODULE
    assert root.children == ()
