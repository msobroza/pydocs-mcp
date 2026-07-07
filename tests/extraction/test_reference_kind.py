"""Pin ReferenceKind shape (spec §4.1)."""

from __future__ import annotations

from enum import StrEnum

import pytest

from pydocs_mcp.extraction.reference_kind import ReferenceKind


def test_reference_kind_is_str_enum() -> None:
    """StrEnum so `r.kind == "calls"` works AND DB rows serialise to plain str."""
    assert issubclass(ReferenceKind, StrEnum)


def test_reference_kind_values_are_the_known_kinds() -> None:
    """Three AST-precise kinds (calls / imports / inherits), the regex-fuzzy
    MENTIONS, the index-time synthetic SIMILAR (embedding-kNN edges), and the
    index-time projected GOVERNS (decisions-as-graph-nodes, spec §D18)."""
    assert {k.value for k in ReferenceKind} == {
        "calls",
        "imports",
        "inherits",
        "mentions",
        "similar",
        "governs",
    }


def test_reference_kind_string_identity() -> None:
    """Each enum stringifies to its lowercase value verbatim — pin the
    on-disk wire format so the row column stays stable across releases.
    """
    assert str(ReferenceKind.CALLS) == "calls"
    assert str(ReferenceKind.IMPORTS) == "imports"
    assert str(ReferenceKind.INHERITS) == "inherits"
    assert str(ReferenceKind.MENTIONS) == "mentions"
    assert str(ReferenceKind.SIMILAR) == "similar"
    assert str(ReferenceKind.GOVERNS) == "governs"
