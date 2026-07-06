"""``build_connection_provider`` lives in storage/factories — layering guard.

db.py must stay pure stdlib (schema + cache lifecycle + FTS rebuild); the
provider factory is composition-root wiring, so it lives next to the other
factories in storage/factories.py. Previously db.py imported
``retrieval.pipeline`` at the bottom of the file behind ``# noqa: E402``
solely for this 3-line wrapper — an inverted db -> retrieval dependency.
"""

from __future__ import annotations

import sqlite3

import pydocs_mcp.db as db_module
from pydocs_mcp.db import open_index_database
from pydocs_mcp.storage.factories import build_connection_provider


async def test_build_connection_provider_opens_valid_db(tmp_path):
    db_file = tmp_path / "factory.db"
    conn = open_index_database(db_file)
    conn.close()

    provider = build_connection_provider(db_file)

    async with provider.acquire() as c:
        assert c.row_factory is sqlite3.Row
        tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"packages", "chunks", "module_members"}.issubset(tables)


def test_db_module_no_longer_exports_the_factory() -> None:
    """Regression guard: db.py must not import retrieval to build providers."""
    assert not hasattr(db_module, "build_connection_provider")
