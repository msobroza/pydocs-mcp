"""Production indexing builds the composite UoW via the SearchBackend."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.factories import build_composite_uow_factory
from pydocs_mcp.storage.search_backend import build_search_backend


@pytest.mark.asyncio
async def test_backend_write_children_yield_dense_capable_uow(tmp_path: Path):
    db_path = tmp_path / "x.db"
    open_index_database(db_path).close()
    backend = build_search_backend(AppConfig.load(), db_path=db_path)
    factory = build_composite_uow_factory(backend.write_uow_children())
    async with factory() as uow:
        # uow.vectors is the TurboQuant child, not NullVectorStore.
        assert type(uow.vectors).__name__ == "TurboQuantUnitOfWork"
