"""Unit tests for :class:`AstPythonChunker` (sub-PR #5, spec §8.1).

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

import os
import sys
from pathlib import Path

import pytest

from pydocs_mcp.extraction.strategies.chunkers import AstPythonChunker
from pydocs_mcp.extraction.config import ChunkingConfig
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.serialization import chunker_registry


def _build(
    content: str, *, path: str = "pkg/mod.py", package: str = "pkg", root: Path | None = None
) -> DocumentNode:
    """Run the chunker on inline content and return the MODULE root."""
    root = root if root is not None else Path("/tmp/fake_root")
    return AstPythonChunker().build_tree(
        path=str(Path(root) / path) if root is not None else path,
        content=content,
        package=package,
        root=Path(root),
    )


def _find_child(
    root: DocumentNode,
    kind: NodeKind,
    title: str | None = None,
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


def test_scattered_imports_split_into_separate_import_blocks(tmp_path: Path) -> None:
    """F22: imports separated by non-import code produce one IMPORT_BLOCK
    per contiguous run, NOT one block spanning the gap. Pre-fix, the
    single block's text swallowed the constant + the function body
    between the two import runs."""
    src = (
        "import os\n"  # line 1 — first run
        "import sys\n"  # line 2 — first run
        "\n"
        "CONST = 42\n"  # line 4 — non-import
        "\n"
        "def helper():\n"  # lines 6-7 — non-import
        "    return CONST\n"
        "\n"
        "import json\n"  # line 9 — second run (lazy)
        "from typing import Any\n"  # line 10 — second run
    )
    root = _build(src, root=tmp_path)
    blocks = [c for c in root.children if c.kind == NodeKind.IMPORT_BLOCK]
    assert len(blocks) == 2, (
        f"expected 2 IMPORT_BLOCK runs, got {len(blocks)} — scattered imports got coalesced again"
    )
    # First run: lines 1-2, both stdlib imports, no swallowed code.
    assert blocks[0].text.splitlines() == ["import os", "import sys"]
    # Second run: lines 9-10, no swallowed CONST/helper.
    assert blocks[1].text.splitlines() == [
        "import json",
        "from typing import Any",
    ]
    # Synthetic ids must be unique so DocumentTreeStore doesn't collide on
    # them in any future per-node persistence path.
    assert blocks[0].node_id != blocks[1].node_id


# ── 4. Class with methods → CLASS + METHOD children, direct text stops ────


def test_class_with_methods_has_method_children_and_split_text(tmp_path: Path) -> None:
    src = (
        "class Foo:\n"
        '    """Foo doc."""\n'
        "    attr = 1\n"
        "    def bar(self):\n"
        "        return 2\n"
        "    def baz(self):\n"
        "        return 3\n"
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


def test_single_line_signature_captured_in_extra_metadata(tmp_path: Path) -> None:
    src = "def foo(a: int) -> int:\n    return a\n"
    root = _build(src, root=tmp_path)
    func = _find_child(root, NodeKind.FUNCTION)
    assert func is not None
    assert func.extra_metadata["signature"] == "def foo(a: int) -> int"


def test_multiline_signature_captured_as_full_header(tmp_path: Path) -> None:
    # The def header spans four physical lines; the captured signature must be
    # the FULL collapsed header, not just ``def foo(`` (the first line).
    src = "def foo(\n    a: int,\n    b: str = 'x',\n) -> dict[str, int]:\n    return {}\n"
    root = _build(src, root=tmp_path)
    func = _find_child(root, NodeKind.FUNCTION)
    assert func is not None
    assert func.extra_metadata["signature"] == "def foo(a: int, b: str = 'x',) -> dict[str, int]"


def test_multiline_async_method_signature_captured(tmp_path: Path) -> None:
    src = (
        "class Svc:\n"
        "    async def fetch(\n"
        "        self,\n"
        "        url: str,\n"
        "    ) -> bytes:\n"
        "        return b''\n"
    )
    root = _build(src, root=tmp_path)
    cls = _find_child(root, NodeKind.CLASS)
    assert cls is not None
    method = next(c for c in cls.children if c.kind == NodeKind.METHOD)
    assert method.extra_metadata["signature"] == "async def fetch(self, url: str,) -> bytes"


# ── 7. Fenced code block in module docstring → CODE_EXAMPLE child of MODULE


def test_module_docstring_fenced_block_yields_code_example(tmp_path: Path) -> None:
    src = '"""Module doc.\n\n```python\nx = 1\n```\n"""\n'
    root = _build(src, root=tmp_path)
    examples = [c for c in root.children if c.kind == NodeKind.CODE_EXAMPLE]
    assert len(examples) == 1
    assert examples[0].text == "x = 1"
    assert examples[0].extra_metadata["language"] == "python"


# ── 8. Fenced code block in function docstring → CODE_EXAMPLE child of FUNC


def test_function_docstring_fenced_block_yields_code_example(tmp_path: Path) -> None:
    src = 'def f():\n    """Func.\n\n    ```python\n    print(1)\n    ```\n    """\n    return 1\n'
    root = _build(src, root=tmp_path)
    func = _find_child(root, NodeKind.FUNCTION)
    assert func is not None
    examples = [c for c in func.children if c.kind == NodeKind.CODE_EXAMPLE]
    assert len(examples) == 1
    assert "print(1)" in examples[0].text


# ── 9. qualified_name is dotted: module, module.func, module.Class.method ──


def test_qualified_names_are_dotted(tmp_path: Path) -> None:
    src = "def f():\n    pass\nclass C:\n    def m(self):\n        pass\n"
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


@pytest.mark.skipif(sys.platform == "win32", reason="os.symlink needs elevated perms on Windows")
def test_symlinked_file_outside_root_keeps_project_relative_qname(tmp_path: Path) -> None:
    """Monorepo symlink case: ``pkg/shared_utils.py`` is a symlink to a file
    that lives OUTSIDE the indexing root (e.g. ``../../lib/shared_utils.py``).

    ``os.walk`` lists symlinked files (``followlinks=False`` only prunes
    symlinked DIRECTORIES from traversal, not individual symlinked files),
    so the discoverer hands this path to the chunker like any other. Pre-fix,
    ``_relative_module_parts`` called ``p.resolve()`` — which follows the
    symlink to its OUTSIDE-root target — before ``relative_to(root)``, so the
    relative_to() raised ValueError and the module id fell back to the bare
    basename stem ``shared_utils`` instead of the project-relative
    ``pkg.shared_utils``. Two same-basename symlinks in different packages
    would then collide on the ``(package, module)`` DocumentTreeStore PK.
    """
    outside_dir = tmp_path.parent / f"{tmp_path.name}_outside_lib"
    outside_dir.mkdir(exist_ok=True)
    real_target = outside_dir / "shared_utils.py"
    real_target.write_text("def helper():\n    return 1\n", encoding="utf-8")

    root = tmp_path / "project"
    pkg_dir = root / "pkg"
    pkg_dir.mkdir(parents=True)
    symlink_path = pkg_dir / "shared_utils.py"
    os.symlink(real_target, symlink_path)

    result = AstPythonChunker().build_tree(
        path=str(symlink_path),
        content=real_target.read_text(encoding="utf-8"),
        package="pkg",
        root=root,
    )
    # The module id must reflect the symlink's LOCATION inside the indexed
    # project (pkg.shared_utils), never the resolved target's bare stem
    # (shared_utils) — that bare form is what two colliding symlinks in
    # different packages would both produce.
    assert result.qualified_name == "pkg.shared_utils"


# ── 16. Fenced code block language tag captured ────────────────────────────


def test_fenced_block_language_tag_captured(tmp_path: Path) -> None:
    src = 'def f():\n    """Doc.\n\n    ```bash\n    echo hi\n    ```\n    """\n    pass\n'
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
    # Sub-PR #5c: make `pkg/` a real package too, so package discovery
    # (`_python_package_root`) treats `tmp_path` as the qname root.
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
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


def test_scattered_imports_children_in_source_order(tmp_path: Path) -> None:
    """A5: when a second import run lives below a def, children must
    still be in source-line order. Pre-fix, _extract_module_children
    appended ALL import blocks first then iterated body for defs —
    the second import run got hoisted above the def it follows in
    source."""
    src = (
        "import os\n"  # line 1 — run1
        "\n"
        "def helper():\n"  # line 3 — def
        "    return 1\n"
        "\n"
        "import json\n"  # line 6 — run2 (BELOW the def)
    )
    root = _build(src, root=tmp_path)
    # Filter to top-level structural children only — start_lines must
    # be monotone non-decreasing.
    lines = [c.start_line for c in root.children]
    assert lines == sorted(lines), (
        f"children out of source order: {[(c.kind.value, c.start_line) for c in root.children]}"
    )
    # Concretely: import_block@1, function@3, import_block@6.
    kinds = [c.kind.value for c in root.children]
    assert kinds == [
        NodeKind.IMPORT_BLOCK.value,
        NodeKind.FUNCTION.value,
        NodeKind.IMPORT_BLOCK.value,
    ]


def test_scattered_imports_three_runs(tmp_path: Path) -> None:
    """T1: F22 only pinned the 2-run case. Three runs catches a regression
    where suffix_counter wraps or doesn't increment past 1."""
    src = (
        "import a\n"  # line 1 — run1
        "\n"
        "x = 1\n"  # line 3 — non-import
        "import b\n"  # line 4 — run2
        "\n"
        "y = 2\n"  # line 6 — non-import
        "import c\n"  # line 7 — run3
    )
    root = _build(src, root=tmp_path)
    blocks = [c for c in root.children if c.kind == NodeKind.IMPORT_BLOCK]
    assert len(blocks) == 3
    # Each block carries exactly one import line.
    assert [b.text.strip() for b in blocks] == ["import a", "import b", "import c"]
    # All three node_ids must be distinct so DocumentTreeStore PK
    # doesn't collide.
    ids = [b.node_id for b in blocks]
    assert len(set(ids)) == 3
    # Suffix scheme: first block keeps the bare __imports__ id; second
    # and third use __imports__1, __imports__2 (1-indexed past zero).
    assert ids[0].endswith(".__imports__")
    assert ids[1].endswith(".__imports__1")
    assert ids[2].endswith(".__imports__2")


def test_imports_only_file_one_block(tmp_path: Path) -> None:
    """T1: imports-only file (no other top-level code) — single
    contiguous run, ONE block, no def/class siblings."""
    src = "import os\nimport sys\nfrom pathlib import Path\n"
    root = _build(src, root=tmp_path)
    blocks = [c for c in root.children if c.kind == NodeKind.IMPORT_BLOCK]
    assert len(blocks) == 1
    # No def or class children to compete with.
    non_imports = [c for c in root.children if c.kind != NodeKind.IMPORT_BLOCK]
    assert non_imports == []


# ── Nested defs get their own FUNCTION nodes ────────────────────────────────


def test_nested_def_gets_own_function_child(tmp_path: Path) -> None:
    """A def inside a function body is emitted as a FUNCTION child of the
    enclosing def — previously invisible to retrieval (PAGEINDEX_DIVS.md F3:
    a benchmark gold was a nested def and no retriever could match it)."""
    src = (
        "def outer():\n"
        "    x = 1\n"
        "    def inner(v):\n"
        '        """Inner doc."""\n'
        "        return v + 1\n"
        "    return inner(x)\n"
    )
    root = _build(src, path="pkg/mod.py", root=tmp_path)
    outer = _find_child(root, NodeKind.FUNCTION, "def outer()")
    assert outer is not None
    inner = next((c for c in outer.children if c.kind == NodeKind.FUNCTION), None)
    assert inner is not None
    assert inner.qualified_name == "pkg.mod.outer.inner"
    assert inner.title == "def inner()"
    assert inner.parent_id == "pkg.mod.outer"
    assert "return v + 1" in inner.text
    assert inner.extra_metadata["docstring"] == "Inner doc."


def test_doubly_nested_def_recurses(tmp_path: Path) -> None:
    src = (
        "def a():\n"
        "    def b():\n"
        "        def c():\n"
        "            return 3\n"
        "        return c()\n"
        "    return b()\n"
    )
    root = _build(src, path="pkg/mod.py", root=tmp_path)
    a = _find_child(root, NodeKind.FUNCTION, "def a()")
    assert a is not None
    b = next(c for c in a.children if c.kind == NodeKind.FUNCTION)
    assert b.qualified_name == "pkg.mod.a.b"
    c = next(ch for ch in b.children if ch.kind == NodeKind.FUNCTION)
    assert c.qualified_name == "pkg.mod.a.b.c"


def test_async_nested_def_inside_method(tmp_path: Path) -> None:
    """Nested capture works through the METHOD path too (shared builder)."""
    src = (
        "class K:\n"
        "    def m(self):\n"
        "        async def helper():\n"
        "            return 1\n"
        "        return helper\n"
    )
    root = _build(src, path="pkg/mod.py", root=tmp_path)
    klass = _find_child(root, NodeKind.CLASS, "class K")
    assert klass is not None
    method = next(c for c in klass.children if c.kind == NodeKind.METHOD)
    helper = next(c for c in method.children if c.kind == NodeKind.FUNCTION)
    assert helper.qualified_name == "pkg.mod.K.m.helper"
    assert helper.title == "async def helper()"


def test_function_without_nested_defs_unchanged(tmp_path: Path) -> None:
    """Non-regression: a flat def still has only CODE_EXAMPLE children."""
    src = "def flat():\n    return 1\n"
    root = _build(src, path="pkg/mod.py", root=tmp_path)
    flat = _find_child(root, NodeKind.FUNCTION, "def flat()")
    assert flat is not None
    assert flat.children == ()
