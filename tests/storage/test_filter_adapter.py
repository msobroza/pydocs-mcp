"""Tests for SqliteFilterAdapter (spec §5.3 AC #7)."""
from __future__ import annotations

import pytest

from pydocs_mcp.storage.filters import All, FieldEq, FieldIn, FieldLike
from pydocs_mcp.storage.sqlite import SqliteFilterAdapter


def test_adapter_field_eq():
    adapter = SqliteFilterAdapter(safe_columns=frozenset({"package", "origin"}))
    where, params = adapter.adapt(FieldEq(field="package", value="fastapi"))
    assert where == "package = ?"
    assert params == ["fastapi"]


def test_adapter_field_in():
    adapter = SqliteFilterAdapter(safe_columns=frozenset({"scope"}))
    where, params = adapter.adapt(FieldIn(field="scope", values=("a", "b")))
    assert where == "scope IN (?, ?)"
    assert params == ["a", "b"]


def test_adapter_field_like():
    adapter = SqliteFilterAdapter(safe_columns=frozenset({"title"}))
    where, params = adapter.adapt(FieldLike(field="title", substring="routing"))
    assert "title LIKE ?" in where
    assert params == ["%routing%"]


def test_adapter_all_joins_with_and():
    adapter = SqliteFilterAdapter(safe_columns=frozenset({"package", "origin"}))
    tree = All(clauses=(
        FieldEq(field="package", value="x"),
        FieldEq(field="origin", value="y"),
    ))
    where, params = adapter.adapt(tree)
    assert "AND" in where
    assert params == ["x", "y"]


def test_adapter_rejects_unsafe_column():
    adapter = SqliteFilterAdapter(safe_columns=frozenset({"package"}))
    with pytest.raises(ValueError, match="not in safe_columns"):
        adapter.adapt(FieldEq(field="foo_bar; DROP TABLE", value="x"))
