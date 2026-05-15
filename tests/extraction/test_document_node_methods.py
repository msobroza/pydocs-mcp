"""Unit tests for DocumentNode.to_pageindex_json + find_node_by_qualified_name.

Pins spec §4.3 serialization contract used by LookupService:
- ``to_pageindex_json`` renames ``start_line`` → ``start_index``,
  ``end_line`` → ``end_index``, emits ``kind`` as a string (not the enum),
  and recurses through ``children`` under the key ``nodes``.
- ``find_node_by_qualified_name`` walks pre-order, returns the first match,
  is iterative (no recursion-limit hit on a 1000-deep tree), and returns
  ``None`` on miss.
"""
from __future__ import annotations

from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind


def _make_node(**overrides) -> DocumentNode:
    """Build a minimal DocumentNode with required fields; overrides merge in."""
    base = dict(
        node_id="pkg.mod",
        qualified_name="pkg.mod",
        title="pkg.mod",
        kind=NodeKind.MODULE,
        source_path="pkg/mod.py",
        start_line=1,
        end_line=10,
        text="",
        content_hash="0" * 40,
    )
    base.update(overrides)
    return DocumentNode(**base)


# ---------------------------------------------------------------------------
# to_pageindex_json
# ---------------------------------------------------------------------------


def test_to_pageindex_json_leaf_module_has_empty_nodes():
    node = _make_node()
    payload = node.to_pageindex_json()
    assert payload["nodes"] == []


def test_to_pageindex_json_recurses_into_children():
    child = _make_node(
        node_id="pkg.mod.foo",
        qualified_name="pkg.mod.foo",
        title="def foo",
        kind=NodeKind.FUNCTION,
        start_line=3,
        end_line=4,
    )
    parent = _make_node(children=(child,))
    payload = parent.to_pageindex_json()
    assert len(payload["nodes"]) == 1
    assert payload["nodes"][0]["node_id"] == "pkg.mod.foo"
    assert payload["nodes"][0]["kind"] == "function"


def test_to_pageindex_json_renames_start_line_to_start_index():
    node = _make_node(start_line=42, end_line=99)
    payload = node.to_pageindex_json()
    assert payload["start_index"] == 42
    assert payload["end_index"] == 99
    # Original keys must be absent — consumers shouldn't have to know either name.
    assert "start_line" not in payload
    assert "end_line" not in payload


def test_to_pageindex_json_uses_kind_dot_value_not_enum():
    node = _make_node(kind=NodeKind.CLASS)
    payload = node.to_pageindex_json()
    assert payload["kind"] == "class"
    assert isinstance(payload["kind"], str)
    assert not isinstance(payload["kind"], NodeKind)


def test_to_pageindex_json_grandchildren_preserved():
    grandchild = _make_node(
        node_id="pkg.mod.C.bar",
        qualified_name="pkg.mod.C.bar",
        title="def bar",
        kind=NodeKind.METHOD,
        start_line=5,
        end_line=6,
    )
    child = _make_node(
        node_id="pkg.mod.C",
        qualified_name="pkg.mod.C",
        title="class C",
        kind=NodeKind.CLASS,
        start_line=4,
        end_line=10,
        children=(grandchild,),
    )
    root = _make_node(children=(child,))
    payload = root.to_pageindex_json()
    assert payload["nodes"][0]["nodes"][0]["node_id"] == "pkg.mod.C.bar"
    assert payload["nodes"][0]["nodes"][0]["kind"] == "method"


def test_to_pageindex_json_includes_summary_and_source_path():
    node = _make_node(
        summary="One-line summary.",
        source_path="pkg/sub/mod.py",
    )
    payload = node.to_pageindex_json()
    assert payload["summary"] == "One-line summary."
    assert payload["source_path"] == "pkg/sub/mod.py"


# ---------------------------------------------------------------------------
# find_node_by_qualified_name
# ---------------------------------------------------------------------------


def test_find_node_by_qualified_name_returns_self_on_match():
    node = _make_node(qualified_name="pkg.mod")
    assert node.find_node_by_qualified_name("pkg.mod") is node


def test_find_node_by_qualified_name_finds_direct_child():
    child = _make_node(
        node_id="pkg.mod.foo",
        qualified_name="pkg.mod.foo",
        kind=NodeKind.FUNCTION,
    )
    parent = _make_node(children=(child,))
    found = parent.find_node_by_qualified_name("pkg.mod.foo")
    assert found is child


def test_find_node_by_qualified_name_finds_grandchild():
    grandchild = _make_node(
        node_id="pkg.mod.C.bar",
        qualified_name="pkg.mod.C.bar",
        kind=NodeKind.METHOD,
    )
    child = _make_node(
        node_id="pkg.mod.C",
        qualified_name="pkg.mod.C",
        kind=NodeKind.CLASS,
        children=(grandchild,),
    )
    root = _make_node(children=(child,))
    assert root.find_node_by_qualified_name("pkg.mod.C.bar") is grandchild


def test_find_node_by_qualified_name_returns_none_on_miss():
    child = _make_node(
        node_id="pkg.mod.foo",
        qualified_name="pkg.mod.foo",
        kind=NodeKind.FUNCTION,
    )
    parent = _make_node(children=(child,))
    assert parent.find_node_by_qualified_name("pkg.mod.bogus") is None


def test_find_node_by_qualified_name_preorder_returns_first_match():
    """Two nodes share a qualified_name; pre-order must return the leftmost."""
    duplicate_a = _make_node(
        node_id="dup#1",
        qualified_name="pkg.dup",
        kind=NodeKind.FUNCTION,
    )
    duplicate_b = _make_node(
        node_id="dup#2",
        qualified_name="pkg.dup",
        kind=NodeKind.FUNCTION,
    )
    root = _make_node(children=(duplicate_a, duplicate_b))
    found = root.find_node_by_qualified_name("pkg.dup")
    assert found is duplicate_a
    assert found.node_id == "dup#1"


def test_find_node_by_qualified_name_handles_1000_deep_tree_iteratively():
    """Build a 1000-level subpackage chain and search the deepest leaf.

    Recursion at this depth would trip Python's default 1000-frame limit;
    the iterative implementation must succeed.
    """
    # Build bottom-up so each level's children tuple is final before its parent.
    leaf = _make_node(
        node_id="root." + ".".join(f"l{i}" for i in range(1000)),
        qualified_name="root." + ".".join(f"l{i}" for i in range(1000)),
    )
    current = leaf
    for i in range(999, -1, -1):
        path = "root" if i == 0 else "root." + ".".join(f"l{j}" for j in range(i))
        current = _make_node(
            node_id=path,
            qualified_name=path,
            children=(current,),
        )
    root = current
    target = leaf.qualified_name
    found = root.find_node_by_qualified_name(target)
    assert found is leaf
