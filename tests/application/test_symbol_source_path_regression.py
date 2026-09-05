"""get_symbol(depth="source") renders a real file path after schema v15.

Regression: ``SymbolSourceService.source_for`` reads
``chunk.metadata.get("source_path")``, but the key was dropped at the SQLite
boundary — a store-loaded chunk always rendered the ``# Source — target``
header WITHOUT the ``· path`` suffix, breaking the §D7 recovery chain
(the file path is the terminal recovery step). Drives the REAL SQLite
repository, not the verbatim in-memory fake.
"""

from pathlib import Path

import pytest

from pydocs_mcp.application.symbol_source import SymbolSourceService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.factories import build_sqlite_uow_factory


@pytest.mark.asyncio
async def test_source_header_carries_persisted_path(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    chunk = Chunk(
        text="def foo(): ...",
        metadata={
            "package": "__project__",
            "qualified_name": "pkg.mod.foo",
            "source_path": "pkg/mod.py",
            "start_line": 1,
            "end_line": 1,
        },
    )
    async with factory() as uow:
        await uow.chunks.upsert((chunk,))
        await uow.commit()

    out = await SymbolSourceService(uow_factory=factory).source_for("pkg.mod.foo")
    assert "# Source — `pkg.mod.foo`  ·  pkg/mod.py" in out
