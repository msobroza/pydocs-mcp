"""build_retrieval_context wires a VectorSearchable dense store from the backend."""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.factories import build_retrieval_context
from pydocs_mcp.storage.protocols import VectorSearchable


def test_context_vector_store_is_vector_searchable(tmp_path: Path) -> None:
    db_path = tmp_path / "x.db"
    open_index_database(db_path).close()
    ctx = build_retrieval_context(db_path, AppConfig.load())
    # The #64 fix: vector_store now answers vector_search (not FTS-only).
    assert isinstance(ctx.vector_store, VectorSearchable)
    assert ctx.uow_factory is not None
