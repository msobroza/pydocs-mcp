"""build_freshness_probe over a genuinely pre-v11 database (no migration run).

``build_freshness_probe._read`` intentionally uses a plain ``sqlite3.connect``
(not ``open_index_database``) to avoid paying migration cost on every freshness
poll. Against a legacy db with no ``index_metadata`` table, the probe must
degrade to ``envelope_info() -> None`` (the documented "no metadata row"
behavior — see ``IndexFreshnessProbe._compute``) rather than crash.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from pydocs_mcp.storage.factories import build_freshness_probe


def _make_legacy_db_no_index_metadata_table(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE packages (name TEXT PRIMARY KEY, embedding_model TEXT)")
    conn.execute("INSERT INTO packages(name, embedding_model) VALUES('__project__', 'bge')")
    conn.commit()
    conn.close()


def test_freshness_probe_degrades_on_legacy_db_without_index_metadata_table(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    _make_legacy_db_no_index_metadata_table(db_path)

    probe = build_freshness_probe(
        db_path=db_path,
        project_root=tmp_path,
        enabled=True,
        ttl_seconds=0.0,
    )

    info = asyncio.run(probe.envelope_info())

    assert info is None
