"""FilterAdapter accepts FieldIn('id', ids) on chunks (late-interaction subset)."""

from __future__ import annotations

from pydocs_mcp.storage.filters import FieldIn
from pydocs_mcp.storage.sqlite import CHUNK_COLUMNS, SqliteFilterAdapter


def test_chunk_columns_includes_id() -> None:
    assert "id" in CHUNK_COLUMNS


def test_field_in_id_emits_sql() -> None:
    adapter = SqliteFilterAdapter()
    where, params = adapter.adapt(FieldIn("id", (1, 2, 3)), target_field="chunk")
    # SqliteFilterAdapter qualifies chunk columns with the ``c.`` alias used
    # in the chunks_fts JOIN chunks shape — just assert the column lands in
    # the WHERE.
    assert "id" in where
    assert tuple(params) == (1, 2, 3)
