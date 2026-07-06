"""_code_example_node — the single owner of the CODE_EXAMPLE identity contract.

The qname scheme (``__example_{i}__``) and the _content_hash recipe are
identity-bearing: chunks.content_hash and tree lookups key off them, so
the .py and .md chunkers must never drift.
"""

from __future__ import annotations

from pydocs_mcp.extraction.model import NodeKind
from pydocs_mcp.extraction.strategies.chunkers._shared import (
    _code_example_node,
    _content_hash,
)


def test_identity_fields_match_the_shared_contract() -> None:
    node = _code_example_node("print('hi')", "python", 2, "pkg.mod.fn", "pkg/mod.py")
    assert node.node_id == "pkg.mod.fn.__example_2__"
    assert node.qualified_name == "pkg.mod.fn.__example_2__"
    assert node.title == "example 2"
    assert node.kind is NodeKind.CODE_EXAMPLE
    assert node.parent_id == "pkg.mod.fn"
    assert node.source_path == "pkg/mod.py"
    assert node.text == "print('hi')"
    assert node.extra_metadata == {"language": "python"}
    assert node.content_hash == _content_hash("print('hi')", NodeKind.CODE_EXAMPLE, "example 2")


def test_line_stub_default_and_explicit_span() -> None:
    # Default matches the .py docstring-stub policy (fence offsets inside a
    # docstring are opaque, so lines are stubbed to 1).
    stubbed = _code_example_node("x", "", 1, "pkg.mod", "pkg/mod.py")
    assert (stubbed.start_line, stubbed.end_line) == (1, 1)
    # The .md chunker passes its real heading span.
    spanned = _code_example_node(
        "x", "", 1, "docs.guide.md#intro", "docs/guide.md", start_line=12, end_line=30
    )
    assert (spanned.start_line, spanned.end_line) == (12, 30)
