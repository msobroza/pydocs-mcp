"""Unit tests for ``extraction/model/tree_flatten.py`` (sub-PR #5).

Pins the direct-text rule (spec §4.1.1) and required metadata keys (§4.4):
- STRUCTURAL_ONLY_KINDS (PACKAGE / SUBPACKAGE) never emit, even with text
- Empty / whitespace-only text never emits
- Emitted chunks carry package, title, kind, origin, source_path, content_hash,
  qualified_name, module
- CODE_EXAMPLE inherits its parent's origin (PYTHON_DEF under code,
  MARKDOWN_SECTION under markdown)
- extra_metadata keys merge WITHOUT overwriting required keys
- module metadata prefers extra_metadata["module"] over qualified_name
"""

from __future__ import annotations

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.model import flatten_to_chunks
from pydocs_mcp.models import ChunkFilterField, ChunkOrigin


def _node(
    kind: NodeKind = NodeKind.MODULE,
    *,
    node_id: str = "pkg.mod",
    qualified_name: str | None = None,
    title: str = "mod",
    text: str = "doc",
    source_path: str = "pkg/mod.py",
    content_hash: str = "deadbeef",
    children: tuple[DocumentNode, ...] = (),
    extra_metadata: dict | None = None,
) -> DocumentNode:
    return DocumentNode(
        node_id=node_id,
        qualified_name=qualified_name if qualified_name is not None else node_id,
        title=title,
        kind=kind,
        source_path=source_path,
        start_line=1,
        end_line=10,
        text=text,
        content_hash=content_hash,
        summary="",
        extra_metadata=extra_metadata or {},
        parent_id=None,
        children=children,
    )


# ── direct-text rule ──────────────────────────────────────────────────────


def test_package_node_skipped_structural_only() -> None:
    node = _node(kind=NodeKind.PACKAGE, text="some text we ignore")
    assert flatten_to_chunks(node, "pkg") == []


def test_subpackage_node_skipped_structural_only() -> None:
    node = _node(kind=NodeKind.SUBPACKAGE, text="also ignored")
    assert flatten_to_chunks(node, "pkg") == []


def test_module_with_empty_text_not_emitted() -> None:
    node = _node(kind=NodeKind.MODULE, text="")
    assert flatten_to_chunks(node, "pkg") == []


def test_module_with_whitespace_only_text_not_emitted() -> None:
    node = _node(kind=NodeKind.MODULE, text="   \n\t  ")
    assert flatten_to_chunks(node, "pkg") == []


# ── basic emission + required metadata keys ──────────────────────────────


def test_module_with_text_emits_single_chunk_with_required_metadata() -> None:
    node = _node(
        kind=NodeKind.MODULE,
        node_id="pkg.mod",
        title="pkg.mod",
        text="Module docstring.",
        source_path="pkg/mod.py",
        content_hash="cafebabe",
    )
    chunks = flatten_to_chunks(node, "pkg")
    assert len(chunks) == 1
    md = chunks[0].metadata

    # Spec §4.4 required keys:
    assert md[ChunkFilterField.PACKAGE.value] == "pkg"
    assert md[ChunkFilterField.TITLE.value] == "pkg.mod"
    assert md["kind"] == NodeKind.MODULE.value
    assert md[ChunkFilterField.ORIGIN.value] == ChunkOrigin.PYTHON_DEF.value
    assert md[ChunkFilterField.SOURCE_PATH.value] == "pkg/mod.py"
    assert md[ChunkFilterField.CONTENT_HASH.value] == "cafebabe"
    assert md["qualified_name"] == "pkg.mod"
    assert md[ChunkFilterField.MODULE.value] == "pkg.mod"
    assert chunks[0].text == "Module docstring."


def test_qualified_name_copied_to_metadata() -> None:
    node = _node(
        kind=NodeKind.FUNCTION,
        node_id="pkg.mod.foo",
        qualified_name="pkg.mod.foo",
        title="foo",
        text="def foo(): ...",
    )
    chunks = flatten_to_chunks(node, "pkg")
    assert chunks[0].metadata["qualified_name"] == "pkg.mod.foo"


# ── hierarchy / recursion ────────────────────────────────────────────────


def test_nested_function_inside_module_emits_both() -> None:
    func = _node(
        kind=NodeKind.FUNCTION,
        node_id="pkg.mod.foo",
        title="foo",
        text="def foo(): pass",
    )
    module = _node(
        kind=NodeKind.MODULE,
        node_id="pkg.mod",
        title="pkg.mod",
        text="Module doc.",
        children=(func,),
    )
    chunks = flatten_to_chunks(module, "pkg")
    assert len(chunks) == 2
    titles = [c.metadata[ChunkFilterField.TITLE.value] for c in chunks]
    assert titles == ["pkg.mod", "foo"]


def test_class_with_methods_emits_hierarchy() -> None:
    m1 = _node(
        kind=NodeKind.METHOD,
        node_id="pkg.mod.Cls.m1",
        title="m1",
        text="def m1(self): pass",
    )
    m2 = _node(
        kind=NodeKind.METHOD,
        node_id="pkg.mod.Cls.m2",
        title="m2",
        text="def m2(self): pass",
    )
    cls = _node(
        kind=NodeKind.CLASS,
        node_id="pkg.mod.Cls",
        title="Cls",
        text="class Cls: ...",
        children=(m1, m2),
    )
    chunks = flatten_to_chunks(cls, "pkg")
    assert len(chunks) == 3
    kinds = [c.metadata["kind"] for c in chunks]
    assert kinds == [NodeKind.CLASS.value, NodeKind.METHOD.value, NodeKind.METHOD.value]
    # All emitted chunks from Python source get PYTHON_DEF origin.
    assert all(
        c.metadata[ChunkFilterField.ORIGIN.value] == ChunkOrigin.PYTHON_DEF.value for c in chunks
    )


def test_subpackage_scaffolding_does_not_block_descendant_emission() -> None:
    """SUBPACKAGE is structural-only but its children still get emitted."""
    module = _node(
        kind=NodeKind.MODULE,
        node_id="pkg.sub.mod",
        title="pkg.sub.mod",
        text="Module doc.",
    )
    sub = _node(
        kind=NodeKind.SUBPACKAGE,
        node_id="pkg.sub",
        title="sub",
        text="",  # structural-only: would skip anyway
        children=(module,),
    )
    chunks = flatten_to_chunks(sub, "pkg")
    assert len(chunks) == 1
    assert chunks[0].metadata[ChunkFilterField.TITLE.value] == "pkg.sub.mod"


# ── CODE_EXAMPLE inherits parent origin ──────────────────────────────────


def test_code_example_under_function_inherits_python_def_origin() -> None:
    example = _node(
        kind=NodeKind.CODE_EXAMPLE,
        node_id="pkg.mod.foo.ex0",
        title="example",
        text=">>> foo(1)\n1",
    )
    func = _node(
        kind=NodeKind.FUNCTION,
        node_id="pkg.mod.foo",
        title="foo",
        text="def foo(x): return x",
        children=(example,),
    )
    chunks = flatten_to_chunks(func, "pkg")
    assert len(chunks) == 2
    func_origin = chunks[0].metadata[ChunkFilterField.ORIGIN.value]
    ex_origin = chunks[1].metadata[ChunkFilterField.ORIGIN.value]
    assert func_origin == ChunkOrigin.PYTHON_DEF.value
    assert ex_origin == ChunkOrigin.PYTHON_DEF.value


def test_code_example_under_markdown_heading_inherits_markdown_section() -> None:
    example = _node(
        kind=NodeKind.CODE_EXAMPLE,
        node_id="README.md#usage.ex0",
        title="example",
        text="print('hi')",
    )
    heading = _node(
        kind=NodeKind.MARKDOWN_HEADING,
        node_id="README.md#usage",
        title="Usage",
        text="How to use the library.",
        source_path="README.md",
        children=(example,),
    )
    chunks = flatten_to_chunks(heading, "pkg")
    assert len(chunks) == 2
    assert chunks[0].metadata[ChunkFilterField.ORIGIN.value] == ChunkOrigin.MARKDOWN_SECTION.value
    assert chunks[1].metadata[ChunkFilterField.ORIGIN.value] == ChunkOrigin.MARKDOWN_SECTION.value


def test_code_example_without_parent_has_no_origin() -> None:
    """Standalone CODE_EXAMPLE (no parent context) still emits but without
    an origin key — matches the spec §4.2 rule that CODE_EXAMPLE inherits
    from its parent rather than having a default of its own."""
    ex = _node(
        kind=NodeKind.CODE_EXAMPLE,
        node_id="ex.standalone",
        title="ex",
        text="print(1)",
    )
    chunks = flatten_to_chunks(ex, "pkg")
    assert len(chunks) == 1
    assert ChunkFilterField.ORIGIN.value not in chunks[0].metadata


# ── MARKDOWN + NOTEBOOK origin mapping ───────────────────────────────────


def test_markdown_heading_gets_markdown_section_origin() -> None:
    node = _node(
        kind=NodeKind.MARKDOWN_HEADING,
        node_id="README.md#intro",
        title="Intro",
        text="Welcome.",
        source_path="README.md",
    )
    chunks = flatten_to_chunks(node, "pkg")
    assert chunks[0].metadata[ChunkFilterField.ORIGIN.value] == ChunkOrigin.MARKDOWN_SECTION.value


def test_notebook_cells_get_notebook_origins() -> None:
    md_cell = _node(
        kind=NodeKind.NOTEBOOK_MARKDOWN_CELL,
        node_id="nb.ipynb#cell0",
        title="cell0",
        text="# Title",
    )
    code_cell = _node(
        kind=NodeKind.NOTEBOOK_CODE_CELL,
        node_id="nb.ipynb#cell1",
        title="cell1",
        text="print('ok')",
    )
    assert (
        flatten_to_chunks(md_cell, "pkg")[0].metadata[ChunkFilterField.ORIGIN.value]
        == ChunkOrigin.NOTEBOOK_MARKDOWN_CELL.value
    )
    assert (
        flatten_to_chunks(code_cell, "pkg")[0].metadata[ChunkFilterField.ORIGIN.value]
        == ChunkOrigin.NOTEBOOK_CODE_CELL.value
    )


# ── extra_metadata merging ───────────────────────────────────────────────


def test_extra_metadata_keys_preserved_alongside_required_keys() -> None:
    node = _node(
        kind=NodeKind.FUNCTION,
        node_id="pkg.mod.foo",
        title="foo",
        text="def foo(): ...",
        extra_metadata={
            "docstring": "Does things.",
            "signature": "def foo() -> None",
            "start_line": 42,
            "end_line": 45,
        },
    )
    md = flatten_to_chunks(node, "pkg")[0].metadata
    assert md["docstring"] == "Does things."
    assert md["signature"] == "def foo() -> None"
    assert md["start_line"] == 42
    assert md["end_line"] == 45
    # Required keys still intact.
    assert md[ChunkFilterField.PACKAGE.value] == "pkg"
    assert md[ChunkFilterField.ORIGIN.value] == ChunkOrigin.PYTHON_DEF.value


def test_extra_metadata_cannot_overwrite_required_keys() -> None:
    """Guard: even if a strategy accidentally sets ``extra_metadata['package']``,
    the flatten step must preserve the canonical value passed as the function
    argument (spec §4.4 says extra_metadata is additive).

    Note: ``module`` is intentionally excluded from this guard — a strategy
    explicitly setting ``extra_metadata['module']`` is the documented hook
    for grouping nested methods under the dotted module path (see
    ``test_module_key_prefers_extra_metadata_module_over_qualified_name``).
    """
    node = _node(
        kind=NodeKind.MODULE,
        node_id="pkg.mod",
        title="pkg.mod",
        text="doc",
        extra_metadata={
            "package": "WRONG",
            "title": "WRONG",
            "origin": "WRONG",
            "qualified_name": "WRONG",
            "source_path": "WRONG",
            "content_hash": "WRONG",
            "kind": "WRONG",
        },
    )
    md = flatten_to_chunks(node, "pkg")[0].metadata
    assert md[ChunkFilterField.PACKAGE.value] == "pkg"
    assert md[ChunkFilterField.TITLE.value] == "pkg.mod"
    assert md[ChunkFilterField.ORIGIN.value] == ChunkOrigin.PYTHON_DEF.value
    assert md["qualified_name"] == "pkg.mod"
    assert md[ChunkFilterField.SOURCE_PATH.value] == "pkg/mod.py"
    assert md[ChunkFilterField.CONTENT_HASH.value] == "deadbeef"
    assert md[ChunkFilterField.MODULE.value] == "pkg.mod"
    assert md["kind"] == NodeKind.MODULE.value


def test_module_key_prefers_extra_metadata_module_over_qualified_name() -> None:
    node = _node(
        kind=NodeKind.FUNCTION,
        node_id="pkg.mod.Cls.meth",
        qualified_name="pkg.mod.Cls.meth",
        title="meth",
        text="def meth(): ...",
        # extraction strategies can group nested nodes under the dotted module
        # path so FTS "module:pkg.mod" filters still work for methods.
        extra_metadata={"module": "pkg.mod"},
    )
    md = flatten_to_chunks(node, "pkg")[0].metadata
    assert md[ChunkFilterField.MODULE.value] == "pkg.mod"


def test_module_key_falls_back_to_qualified_name_when_orphan_node() -> None:
    """No MODULE ancestor + no ``extra_metadata['module']``: last-resort
    fallback to the node's own qualified_name (orphan trees that bypass
    the MODULE wrapper — rare; mostly notebook code-cell roots)."""
    node = _node(
        kind=NodeKind.FUNCTION,
        node_id="pkg.mod.Cls.meth",
        qualified_name="pkg.mod.Cls.meth",
        title="meth",
        text="def meth(): ...",
        extra_metadata={},
    )
    md = flatten_to_chunks(node, "pkg")[0].metadata
    assert md[ChunkFilterField.MODULE.value] == "pkg.mod.Cls.meth"


def test_class_and_method_nested_in_module_inherit_module_ancestor() -> None:
    """CLASS / METHOD nested under a MODULE node MUST take the module's
    qualified_name for ``chunks.module``, NOT their own qualified_name.

    Pre-fix: a CLASS without ``extra_metadata['module']`` fell back to
    its own ``qualified_name = 'pkg.mod.Cls'``, polluting the column
    that retrieval filters use to group results by module. This pins
    the module-ancestor walk done by ``flatten_to_chunks``.
    """
    method = _node(
        kind=NodeKind.METHOD,
        node_id="pkg.mod.Cls.m",
        qualified_name="pkg.mod.Cls.m",
        title="m",
        text="def m(self): pass",
        extra_metadata={},  # no explicit module — must inherit from ancestor
    )
    cls = _node(
        kind=NodeKind.CLASS,
        node_id="pkg.mod.Cls",
        qualified_name="pkg.mod.Cls",
        title="Cls",
        text="class Cls: ...",
        children=(method,),
        extra_metadata={},  # no explicit module — must inherit from ancestor
    )
    module = _node(
        kind=NodeKind.MODULE,
        node_id="pkg.mod",
        qualified_name="pkg.mod",
        title="pkg.mod",
        text="Module doc.",
        children=(cls,),
    )

    chunks = flatten_to_chunks(module, "pkg")
    by_kind = {c.metadata["kind"]: c for c in chunks}
    # All three chunks emit; module ancestor 'pkg.mod' wins for every one.
    assert by_kind[NodeKind.MODULE.value].metadata[ChunkFilterField.MODULE.value] == "pkg.mod"
    assert by_kind[NodeKind.CLASS.value].metadata[ChunkFilterField.MODULE.value] == "pkg.mod"
    assert by_kind[NodeKind.METHOD.value].metadata[ChunkFilterField.MODULE.value] == "pkg.mod"


def test_import_block_inherits_module_ancestor_for_chunks_module() -> None:
    """IMPORT_BLOCK is the FIRST child a Python chunker emits under MODULE.
    Its ``qualified_name`` is the synthetic ``pkg.mod.__imports__`` — must
    NOT leak into ``chunks.module``; the module ancestor wins (F1)."""
    import_block = _node(
        kind=NodeKind.IMPORT_BLOCK,
        node_id="pkg.mod.__imports__",
        qualified_name="pkg.mod.__imports__",
        title="imports",
        text="import os\nimport sys",
        extra_metadata={},
    )
    module = _node(
        kind=NodeKind.MODULE,
        node_id="pkg.mod",
        qualified_name="pkg.mod",
        title="pkg.mod",
        text="",
        children=(import_block,),
    )
    chunks = flatten_to_chunks(module, "pkg")
    [ib_chunk] = chunks  # module has empty text → only import_block emits
    assert ib_chunk.metadata[ChunkFilterField.MODULE.value] == "pkg.mod"


def test_code_example_under_function_inherits_module_ancestor() -> None:
    """CODE_EXAMPLE is a GRANDCHILD of MODULE (under FUNCTION/METHOD/
    CLASS/MARKDOWN_HEADING). The recursion must thread current_module
    through the intermediate parent so the code-example chunk's
    ``chunks.module`` is the module path, NOT the parent's
    qualified_name (F1)."""
    code_ex = _node(
        kind=NodeKind.CODE_EXAMPLE,
        node_id="pkg.mod.foo.ex0",
        qualified_name="pkg.mod.foo.ex0",
        title="example",
        text="print('hi')",
        extra_metadata={},
    )
    func = _node(
        kind=NodeKind.FUNCTION,
        node_id="pkg.mod.foo",
        qualified_name="pkg.mod.foo",
        title="foo",
        text="def foo(): pass",
        children=(code_ex,),
        extra_metadata={},
    )
    module = _node(
        kind=NodeKind.MODULE,
        node_id="pkg.mod",
        qualified_name="pkg.mod",
        title="pkg.mod",
        text="doc",
        children=(func,),
    )
    chunks = flatten_to_chunks(module, "pkg")
    by_kind = {c.metadata["kind"]: c for c in chunks}
    assert by_kind[NodeKind.CODE_EXAMPLE.value].metadata[ChunkFilterField.MODULE.value] == "pkg.mod"


def test_explicit_extra_metadata_module_still_wins_under_module_ancestor() -> None:
    """Precedence: an explicit ``extra_metadata['module']`` on a child
    node beats the tracked MODULE ancestor. Lets inspect-mode chunkers
    override the ancestor path when the live module differs from the
    file's static qualified_name (rare; reserved for re-export cases)."""
    method = _node(
        kind=NodeKind.METHOD,
        node_id="pkg.mod.Cls.m",
        qualified_name="pkg.mod.Cls.m",
        title="m",
        text="def m(self): pass",
        extra_metadata={"module": "pkg.reexported"},
    )
    module = _node(
        kind=NodeKind.MODULE,
        node_id="pkg.mod",
        qualified_name="pkg.mod",
        title="pkg.mod",
        text="",
        children=(method,),
    )
    chunks = flatten_to_chunks(module, "pkg")
    [meth_chunk] = chunks
    assert meth_chunk.metadata[ChunkFilterField.MODULE.value] == "pkg.reexported"


# ── edge: empty tree / leaf skipping ──────────────────────────────────────


def test_empty_intermediate_node_skipped_but_children_emit() -> None:
    """A MODULE with empty .text skips its own emission but children still emit."""
    child = _node(
        kind=NodeKind.FUNCTION,
        node_id="pkg.mod.foo",
        title="foo",
        text="def foo(): pass",
    )
    module = _node(
        kind=NodeKind.MODULE,
        node_id="pkg.mod",
        title="pkg.mod",
        text="",  # skip me
        children=(child,),
    )
    chunks = flatten_to_chunks(module, "pkg")
    assert len(chunks) == 1
    assert chunks[0].metadata[ChunkFilterField.TITLE.value] == "foo"
