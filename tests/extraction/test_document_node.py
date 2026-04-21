"""Unit tests for DocumentNode + NodeKind + STRUCTURAL_ONLY_KINDS (Task 2 — sub-PR #5).

Pins spec §4.2-§4.4 invariants:
- 11 NodeKind values with lowercase ``.value`` strings.
- STRUCTURAL_ONLY_KINDS is a frozenset of exactly PACKAGE + SUBPACKAGE.
- DocumentNode is frozen (FrozenInstanceError on mutation) + slotted
  (typo guard; unknown attribute raises).
- ``qualified_name`` is a first-class field — not reached via extra_metadata.
- Defaults: summary="", extra_metadata={}, parent_id=None, children=().
- ``children`` is a tuple (immutable) so the whole tree is deeply immutable.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError

import pytest

from pydocs_mcp.extraction.document_node import (
    STRUCTURAL_ONLY_KINDS,
    DocumentNode,
    NodeKind,
)


def _make_node(**overrides) -> DocumentNode:
    """Build a minimal DocumentNode with required fields; overrides merge in."""
    base = dict(
        node_id="pkg.mod.foo",
        qualified_name="pkg.mod.foo",
        title="def foo",
        kind=NodeKind.FUNCTION,
        source_path="pkg/mod.py",
        start_line=1,
        end_line=5,
        text="def foo(): ...",
        content_hash="0" * 40,
    )
    base.update(overrides)
    return DocumentNode(**base)


def test_node_kind_values_complete():
    """All 11 NodeKind values present with lowercase ``.value`` strings."""
    expected = {
        "package",
        "subpackage",
        "module",
        "import_block",
        "class",
        "function",
        "method",
        "markdown_heading",
        "notebook_markdown_cell",
        "notebook_code_cell",
        "code_example",
    }
    actual = {k.value for k in NodeKind}
    assert actual == expected
    assert len(NodeKind) == 11
    # StrEnum: values must equal their string form (lowercase)
    for k in NodeKind:
        assert k.value == k.value.lower()
        assert isinstance(k.value, str)


def test_structural_only_kinds_is_frozenset_of_two():
    """Exactly PACKAGE + SUBPACKAGE — spec §4.1.1 scaffolding-only kinds."""
    assert isinstance(STRUCTURAL_ONLY_KINDS, frozenset)
    assert STRUCTURAL_ONLY_KINDS == frozenset(
        {NodeKind.PACKAGE, NodeKind.SUBPACKAGE},
    )
    assert len(STRUCTURAL_ONLY_KINDS) == 2


def test_document_node_frozen():
    """Mutation raises FrozenInstanceError — value semantics guaranteed."""
    node = _make_node()
    with pytest.raises(FrozenInstanceError):
        node.title = "changed"  # type: ignore[misc]


def test_document_node_slots_rejects_unknown_attr():
    """slots=True blocks typos like node.titel = ...; unknown attr raises.

    On a slotted frozen dataclass, setting an undeclared attribute raises
    either FrozenInstanceError (dataclass wrapper refuses __setattr__) or
    TypeError / AttributeError (slots + frozen combo, version-dependent
    depending on whether the dataclass or the slots machinery trips first).
    All three are correct protection; accept any of them.
    """
    node = _make_node()
    with pytest.raises((AttributeError, TypeError, FrozenInstanceError)):
        node.typo_field = "boom"  # type: ignore[attr-defined]


def test_document_node_defaults():
    """Defaults per spec §4.3: summary='', extra_metadata={}, parent_id=None, children=()."""
    node = _make_node()
    assert node.summary == ""
    assert node.extra_metadata == {}
    assert node.parent_id is None
    assert node.children == ()
    # Children default must be a tuple (not list) — deep immutability.
    assert isinstance(node.children, tuple)


def test_document_node_qualified_name_first_class_field():
    """qualified_name is a DocumentNode field — NOT stored under extra_metadata.

    Pins spec §4.4 note: flatten copies qualified_name directly onto the
    Chunk, not via the extra_metadata dict. The field must be addressable
    as ``node.qualified_name`` at the dataclass level.
    """
    node = _make_node(qualified_name="requests.adapters.HTTPAdapter")
    assert node.qualified_name == "requests.adapters.HTTPAdapter"
    # Must NOT live in extra_metadata.
    assert "qualified_name" not in node.extra_metadata
    # Must be a declared dataclass field (slots guarantee this too, but pin it).
    assert "qualified_name" in set(node.__dataclass_fields__)


def test_document_node_nested_children_immutable():
    """Nested tree; children is a tuple; grandchildren accessible and immutable."""
    grandchild = _make_node(
        node_id="pkg.mod.Cls.method",
        qualified_name="pkg.mod.Cls.method",
        title="def method",
        kind=NodeKind.METHOD,
        start_line=3,
        end_line=4,
        text="    def method(self): ...",
        parent_id="pkg.mod.Cls",
    )
    child = _make_node(
        node_id="pkg.mod.Cls",
        qualified_name="pkg.mod.Cls",
        title="class Cls",
        kind=NodeKind.CLASS,
        start_line=2,
        end_line=4,
        text="class Cls:\n",
        parent_id="pkg.mod",
        children=(grandchild,),
    )
    root = _make_node(
        node_id="pkg.mod",
        qualified_name="pkg.mod",
        title="module pkg.mod",
        kind=NodeKind.MODULE,
        start_line=1,
        end_line=4,
        text='"""Module docstring."""\n',
        children=(child,),
    )

    # Tuple (not list) at every level — deep immutability.
    assert isinstance(root.children, tuple)
    assert isinstance(root.children[0].children, tuple)
    # Grandchild reachable through the tree.
    assert root.children[0].children[0].node_id == "pkg.mod.Cls.method"
    assert root.children[0].children[0].kind is NodeKind.METHOD
    # Mutating the tuple must fail.
    with pytest.raises((AttributeError, TypeError)):
        root.children[0].children[0] = grandchild  # type: ignore[index]


def test_document_node_extra_metadata_optional_mapping():
    """Passing a dict for extra_metadata is stored and typed as Mapping.

    The field is annotated ``Mapping[str, Any]`` (read-only protocol) — a
    plain ``dict`` satisfies it and is stored as-is. Pin both the Mapping
    shape and the round-trip identity so future refactors (e.g., wrapping
    in ``MappingProxyType`` at construction time) don't silently break
    callers relying on either aspect.
    """
    meta = {"inherits_from": ["Base"], "docstring": "doc."}
    node = _make_node(
        kind=NodeKind.CLASS,
        title="class X",
        extra_metadata=meta,
    )
    # Mapping protocol access.
    assert isinstance(node.extra_metadata, Mapping)
    assert node.extra_metadata["inherits_from"] == ["Base"]
    assert node.extra_metadata["docstring"] == "doc."
    # Round-trip: value is stored unchanged (equality, not identity — a
    # future wrapping pass may still satisfy the equality contract).
    assert dict(node.extra_metadata) == meta
