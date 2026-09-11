"""The embed skip must count persisted copies, not just recognise hashes.

``IndexingService._diff_merge_chunks`` is deliberately a MULTISET diff (#69): a
package may legitimately carry several chunks with an identical identity tuple
and therefore an identical ``content_hash`` — a README section pasted twice, a
boilerplate docstring repeated across modules. For each hash it keeps
``min(existing, incoming)`` rows and inserts the incoming excess as genuinely
new rows.

Regression: once the skip set actually fired (it was a permanent no-op before),
the embed stage tested plain SET membership against it. A second copy of an
already-persisted hash was therefore never embedded — but the diff-merge still
inserted it as a new row, so it was persisted WITHOUT a vector, and stayed
vectorless on every later pass because its hash was in the skip set from then
on. Only ``index --force`` recovered it.

The skip set now carries the persisted multiplicity per hash and the embed stage
spends it as a budget: the first N copies are treated as already vectorised, the
rest are embedded.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import CountingEmbedder, MockEmbedder

_SECTION = "## Usage\n\nRun the tool with the default profile.\n"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "multiplicity.db"
    open_index_database(path).close()
    return path


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "GUIDE.md").write_text("# Guide\n\n" + _SECTION)
    return root


def _index(monkeypatch, db_path: Path, project_dir: Path) -> CountingEmbedder:
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.storage.factories import build_project_indexer

    counting = CountingEmbedder(inner=MockEmbedder())
    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: counting)
    bundle = build_project_indexer(AppConfig.load(), db_path, use_inspect=False, inspect_depth=None)
    asyncio.run(bundle.orchestrator.index_project(project_dir, include_dependencies=False))
    return counting


def _rows(db_path: Path, title: str) -> list[tuple[str, int]]:
    """``(content_hash, embedded)`` for every persisted chunk with `title`."""
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT content_hash, embedded FROM chunks WHERE title=? ORDER BY id", (title,)
        ).fetchall()
    finally:
        conn.close()


def test_a_second_copy_of_a_persisted_chunk_still_gets_its_vector(
    monkeypatch, db_path: Path, project_dir: Path
) -> None:
    _index(monkeypatch, db_path, project_dir)
    (first,) = _rows(db_path, "Usage")
    assert first[1] == 1, "baseline: the single copy must be embedded"

    # Duplicate the section verbatim: same package/module/title/text, same hash.
    (project_dir / "GUIDE.md").write_text("# Guide\n\n" + _SECTION + "\n" + _SECTION)
    counting = _index(monkeypatch, db_path, project_dir)

    rows = _rows(db_path, "Usage")
    assert len(rows) == 2, f"the diff-merge must persist both copies, got {rows!r}"
    assert {h for h, _ in rows} == {first[0]}, "both copies share the hash by construction"
    assert all(embedded == 1 for _, embedded in rows), (
        f"the new duplicate row was persisted WITHOUT a vector: {rows!r}"
    )
    # Exactly the one new copy was embedded — the budget must not over-embed either.
    assert sum(n for _, n in counting.calls) == 1, counting.calls


def test_an_unchanged_duplicate_pair_is_still_a_free_pass(
    monkeypatch, db_path: Path, project_dir: Path
) -> None:
    """Budget accounting must settle: two persisted copies, two incoming, zero embeds."""
    (project_dir / "GUIDE.md").write_text("# Guide\n\n" + _SECTION + "\n" + _SECTION)
    _index(monkeypatch, db_path, project_dir)

    counting = _index(monkeypatch, db_path, project_dir)

    assert counting.calls == []
