"""Decorator capture in :class:`AstPythonChunker`.

Functions / methods / classes record their decorators as
``extra_metadata["decorators"]`` (a tuple of ``@<decorator>`` labels) so
the PageIndex tree-reasoning step can surface role markers (``@property``,
``@app.route('/login')``, ``@staticmethod``, …) to the LLM. Call decorators
INCLUDE their arguments (``@app.route('/login')``) — the route path / call
args carry signal for query matching — bounded by ``_DECORATOR_LABEL_MAX_CHARS``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.strategies.chunkers.ast_python import (
    AstPythonChunker,
    _decorator_labels,
)


def _build(content: str, *, path: str = "pkg/mod.py", package: str = "pkg") -> DocumentNode:
    root = Path("/tmp/fake_root")
    return AstPythonChunker().build_tree(
        path=str(root / path), content=content, package=package, root=root
    )


def _find(root: DocumentNode, kind: NodeKind) -> DocumentNode:
    for c in root.children:
        if c.kind == kind:
            return c
    raise AssertionError(f"no {kind} child")


# ── _decorator_labels unit ────────────────────────────────────────────────


def _decs(code: str) -> tuple[str, ...]:
    return _decorator_labels(ast.parse(code).body[0].decorator_list)


def test_labels_bare_names_in_source_order() -> None:
    assert _decs("@property\n@staticmethod\ndef f():\n    pass\n") == (
        "@property",
        "@staticmethod",
    )


def test_labels_call_decorator_includes_arguments() -> None:
    assert _decs("@app.route('/login')\ndef f():\n    pass\n") == ("@app.route('/login')",)


def test_labels_dotted_call_includes_arguments() -> None:
    assert _decs("@functools.wraps(g)\ndef f():\n    pass\n") == ("@functools.wraps(g)",)


def test_labels_empty_when_no_decorators() -> None:
    assert _decs("def f():\n    pass\n") == ()


def test_labels_complex_decorator_falls_back_to_unparse() -> None:
    # A subscript decorator isn't a dotted name -> canonical_dotted returns
    # None -> bounded ast.unparse fallback.
    assert _decs("@registry[0]\ndef f():\n    pass\n") == ("@registry[0]",)


# ── chunker integration ───────────────────────────────────────────────────


def test_function_node_captures_decorators() -> None:
    func = _find(_build("@property\ndef foo():\n    return 1\n"), NodeKind.FUNCTION)
    assert func.extra_metadata["decorators"] == ("@property",)


def test_class_node_captures_decorators() -> None:
    cls = _find(_build("@dataclass\nclass Foo:\n    pass\n"), NodeKind.CLASS)
    assert cls.extra_metadata["decorators"] == ("@dataclass",)


def test_method_node_captures_decorators() -> None:
    cls = _find(
        _build("class Foo:\n    @staticmethod\n    def bar():\n        return 1\n"),
        NodeKind.CLASS,
    )
    method = next(c for c in cls.children if c.kind == NodeKind.METHOD)
    assert method.extra_metadata["decorators"] == ("@staticmethod",)


def test_function_without_decorators_records_empty_tuple() -> None:
    func = _find(_build("def foo():\n    return 1\n"), NodeKind.FUNCTION)
    assert func.extra_metadata["decorators"] == ()


def test_args_included_through_build_tree() -> None:
    # End-to-end (not just the _decorator_labels unit): a call decorator's
    # arguments are INCLUDED at the chunker level too — the route path is
    # high-signal for query matching.
    func = _find(_build("@app.route('/login')\ndef login():\n    return 1\n"), NodeKind.FUNCTION)
    assert func.extra_metadata["decorators"] == ("@app.route('/login')",)


def test_decorator_label_is_length_bounded() -> None:
    long_name = "a" * 200
    func = _find(_build(f"@{long_name}\ndef f():\n    pass\n"), NodeKind.FUNCTION)
    (label,) = func.extra_metadata["decorators"]
    assert len(label) <= 100


def test_decorators_metadata_not_persisted_to_chunk_columns() -> None:
    # tree_flatten merges extra_metadata (incl. "decorators") into in-memory
    # chunk metadata, but the chunks table persists only schema columns — so
    # "decorators" must NOT survive _chunk_to_row. (qualified_name DOES, since
    # it's the tree-reasoning join key.)
    from pydocs_mcp.models import Chunk
    from pydocs_mcp.storage.sqlite import _chunk_to_row

    chunk = Chunk(
        text="def foo(): ...",
        metadata={
            "package": "pkg",
            "qualified_name": "pkg.mod.foo",
            "decorators": ["@property"],
        },
    )
    row = _chunk_to_row(chunk)
    assert "decorators" not in row
    assert row["qualified_name"] == "pkg.mod.foo"


def test_decorators_survive_document_tree_serialization() -> None:
    from pydocs_mcp.storage.sqlite import (
        _deserialize_tree_from_json,
        _serialize_tree_to_json,
    )

    root = _build("@property\ndef foo():\n    return 1\n")
    restored = _deserialize_tree_from_json(_serialize_tree_to_json(root))
    func = _find(restored, NodeKind.FUNCTION)
    # JSON has no tuple type: a persisted tuple round-trips as a list. The
    # render path only iterates it, so list vs tuple is immaterial there.
    assert list(func.extra_metadata["decorators"]) == ["@property"]
