"""Pin ReferenceKind shape (spec §4.1)."""

from __future__ import annotations

from enum import StrEnum

import pytest

from pydocs_mcp.extraction.reference_kind import ReferenceKind


def test_reference_kind_is_str_enum() -> None:
    """StrEnum so `r.kind == "calls"` works AND DB rows serialise to plain str."""
    assert issubclass(ReferenceKind, StrEnum)


def test_reference_kind_values_are_the_four_kinds() -> None:
    """Sub-PR #5c lands MENTIONS — regex-fuzzy backtick-quoted dotted
    names captured from markdown. Joins the three AST-precise kinds
    (calls / imports / inherits) as the fourth wire value."""
    assert {k.value for k in ReferenceKind} == {
        "calls",
        "imports",
        "inherits",
        "mentions",
    }


def test_reference_kind_string_identity() -> None:
    """Each enum stringifies to its lowercase value verbatim — pin the
    on-disk wire format so the row column stays stable across releases.
    """
    assert str(ReferenceKind.CALLS) == "calls"
    assert str(ReferenceKind.IMPORTS) == "imports"
    assert str(ReferenceKind.INHERITS) == "inherits"
    assert str(ReferenceKind.MENTIONS) == "mentions"
