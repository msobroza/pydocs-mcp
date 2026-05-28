"""Pin NodeReference value object shape (spec §4.2)."""

from __future__ import annotations

import dataclasses

import pytest

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference


def _ref(**kw) -> NodeReference:
    base = dict(
        from_package="pkg",
        from_node_id="pkg.mod.fn",
        to_name="other.symbol",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    base.update(kw)
    return NodeReference(**base)


def test_node_reference_is_frozen_slotted_dataclass() -> None:
    r = _ref()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.from_package = "other"  # type: ignore[misc]
    assert not hasattr(r, "__dict__")


def test_node_reference_holds_all_fields() -> None:
    r = _ref(to_node_id="pkg.other.symbol")
    assert r.from_package == "pkg"
    assert r.from_node_id == "pkg.mod.fn"
    assert r.to_name == "other.symbol"
    assert r.to_node_id == "pkg.other.symbol"
    assert r.kind is ReferenceKind.CALLS


def test_node_reference_to_node_id_defaults_to_none() -> None:
    r = NodeReference(
        from_package="pkg",
        from_node_id="pkg.mod.fn",
        to_name="os.path.join",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    assert r.to_node_id is None


def test_node_reference_equality_by_value() -> None:
    assert _ref() == _ref()
    assert _ref(kind=ReferenceKind.IMPORTS) != _ref(kind=ReferenceKind.CALLS)
