"""Unit tests for ``build_package_tree`` (Task 21 — sub-PR #5, spec §12.2).

Pins the module-path trie assembly rules:
- Root is always NodeKind.PACKAGE with ``package`` as its qualified_name.
- Intermediate dotted segments synthesize SUBPACKAGE nodes.
- Input MODULE ``DocumentNode``s become leaves without modification.
- STRUCTURAL_ONLY_KINDS (PACKAGE/SUBPACKAGE) synthesized here never carry text.
- Direct-name collision (``pkg.auth`` exists alongside ``pkg.auth.basic``) is
  resolved by placing the existing module as a leaf under the SUBPACKAGE.
- ``extra_metadata["module_count"]`` equals ``len(trees)``.
- Children are deterministically sorted — the trie walks segments in
  ``sorted()`` order so callers get a stable arborescence regardless of
  dict iteration order.
"""
from __future__ import annotations

from pydocs_mcp.extraction.document_node import (
    STRUCTURAL_ONLY_KINDS,
    DocumentNode,
    NodeKind,
)
from pydocs_mcp.extraction.package_tree import build_package_tree


def _module(name: str) -> DocumentNode:
    """Minimal MODULE DocumentNode — unique hash lets tests assert leaf identity."""
    return DocumentNode(
        node_id=name,
        qualified_name=name,
        title=name,
        kind=NodeKind.MODULE,
        source_path=f"{name.replace('.', '/')}.py",
        start_line=1,
        end_line=10,
        text=f"module {name}",
        content_hash=f"hash-{name}",
    )


def test_empty_trees() -> None:
    root = build_package_tree("pkg", {})
    assert root.kind is NodeKind.PACKAGE
    assert root.qualified_name == "pkg"
    assert root.children == ()
    assert root.extra_metadata["module_count"] == 0


def test_single_module_package() -> None:
    """Single-leaf case: one MODULE directly under the PACKAGE root."""
    mod = _module("pkg")
    root = build_package_tree("pkg", {"pkg": mod})
    assert root.kind is NodeKind.PACKAGE
    assert len(root.children) == 1
    assert root.children[0] is mod
    assert root.children[0].kind is NodeKind.MODULE


def test_flat_modules() -> None:
    """Flat layout: two siblings under PACKAGE, alphabetically sorted."""
    a = _module("pkg.a")
    b = _module("pkg.b")
    root = build_package_tree("pkg", {"pkg.b": b, "pkg.a": a})
    assert [c.qualified_name for c in root.children] == ["pkg.a", "pkg.b"]
    assert all(c.kind is NodeKind.MODULE for c in root.children)


def test_subpackage_nesting() -> None:
    """Mixed layout: SUBPACKAGE(pkg.a) wrapping MODULE(pkg.a.deep) + MODULE(pkg.b)."""
    deep = _module("pkg.a.deep")
    b = _module("pkg.b")
    root = build_package_tree("pkg", {"pkg.a.deep": deep, "pkg.b": b})
    assert len(root.children) == 2
    sub_a = root.children[0]
    leaf_b = root.children[1]
    assert sub_a.kind is NodeKind.SUBPACKAGE
    assert sub_a.qualified_name == "pkg.a"
    assert sub_a.title == "a"
    assert len(sub_a.children) == 1
    assert sub_a.children[0] is deep
    assert leaf_b is b


def test_three_level_nesting() -> None:
    """PACKAGE → SUBPACKAGE → SUBPACKAGE → MODULE chain for a single-leaf path."""
    c = _module("pkg.a.b.c")
    root = build_package_tree("pkg", {"pkg.a.b.c": c})
    assert root.kind is NodeKind.PACKAGE
    sub_a = root.children[0]
    assert sub_a.kind is NodeKind.SUBPACKAGE
    assert sub_a.qualified_name == "pkg.a"
    sub_b = sub_a.children[0]
    assert sub_b.kind is NodeKind.SUBPACKAGE
    assert sub_b.qualified_name == "pkg.a.b"
    leaf = sub_b.children[0]
    assert leaf is c
    assert leaf.kind is NodeKind.MODULE


def test_subpackage_and_leaf_module_at_same_prefix() -> None:
    """``pkg.auth`` AND ``pkg.auth.basic`` both present — mimics an __init__.py
    plus a submodule. Semantics: the module at the subpackage name becomes a
    leaf under the synthesized SUBPACKAGE, not a replacement for it."""
    auth = _module("pkg.auth")
    basic = _module("pkg.auth.basic")
    root = build_package_tree("pkg", {"pkg.auth": auth, "pkg.auth.basic": basic})
    assert len(root.children) == 1
    sub_auth = root.children[0]
    assert sub_auth.kind is NodeKind.SUBPACKAGE
    assert sub_auth.qualified_name == "pkg.auth"
    # Both the package __init__-equivalent MODULE and its submodule appear
    # as leaves under the synthesized SUBPACKAGE.
    child_qnames = {c.qualified_name for c in sub_auth.children}
    assert child_qnames == {"pkg.auth", "pkg.auth.basic"}
    # Both leaves retain their MODULE kind.
    assert all(c.kind is NodeKind.MODULE for c in sub_auth.children)


def test_module_count_in_metadata() -> None:
    trees = {
        "pkg.a": _module("pkg.a"),
        "pkg.b.c": _module("pkg.b.c"),
        "pkg.b.d": _module("pkg.b.d"),
    }
    root = build_package_tree("pkg", trees)
    assert root.extra_metadata["module_count"] == 3
    assert root.extra_metadata["module"] == "pkg"


def test_root_is_package_kind() -> None:
    root = build_package_tree("whatever", {"whatever.x": _module("whatever.x")})
    assert root.kind is NodeKind.PACKAGE
    assert root.kind in STRUCTURAL_ONLY_KINDS


def test_structural_nodes_carry_no_text() -> None:
    """Spec §4.1.1: STRUCTURAL_ONLY_KINDS never carry text."""
    trees = {"pkg.a.b": _module("pkg.a.b"), "pkg.c": _module("pkg.c")}
    root = build_package_tree("pkg", trees)
    assert root.text == ""
    sub_a = next(c for c in root.children if c.kind is NodeKind.SUBPACKAGE)
    assert sub_a.text == ""


def test_children_are_tuple_immutable() -> None:
    """``children`` must be a tuple for deep immutability across async tasks."""
    root = build_package_tree("pkg", {"pkg.a": _module("pkg.a")})
    assert isinstance(root.children, tuple)
    # Nested SUBPACKAGE also has tuple children.
    trees = {"pkg.a.deep": _module("pkg.a.deep")}
    root2 = build_package_tree("pkg", trees)
    assert isinstance(root2.children, tuple)
    assert isinstance(root2.children[0].children, tuple)
