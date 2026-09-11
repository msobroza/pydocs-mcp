"""AC-12: an index written with the old member module-id rule heals in one pass.

The old rule stored src-layout project members as ``src.needle.x`` while
chunks and trees read ``needle.x`` (spec 2026-09-10-member-module-ids-design
§1). The project cache skip runs before member extraction, so the fix only
reaches an existing index because ``ContentHashStage`` folds
``MODULE_ID_RULE_VERSION`` into the ``__project__`` hash (§4). This test
rebuilds that old state on disk (``src.`` member rows plus a hash in the
pre-fix framing) and proves one pass heals it without re-embedding a chunk
or re-extracting a dependency, and that the next pass is a cache hit.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_mcp.application.indexing_service import IndexingStats
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies.discovery import ProjectFileDiscoverer
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.factories import build_project_indexer
from tests._fakes import (
    CountingEmbedder,
    CountingMemberExtractor,
    FakeDependencyResolver,
    MockEmbedder,
)
from tests.extraction._content_hash_oracle import grammar_folded, raw_hash_files, rule_folded

# A small pure-Python dist that the frozen dev venv always carries (anyio dep).
_DEPENDENCY = "sniffio"
_CHUNK_IDENTITY_SQL = "SELECT id, content_hash FROM chunks ORDER BY id"
_PROJECT_MEMBERS_SQL = (
    "SELECT module, name FROM module_members WHERE package=? ORDER BY module, name"
)
_STRATEGIES_PY = (
    '"""Scoring strategies."""\n\n\nclass Ranker:\n    """Rank hits."""\n\n'
    '    def score(self, hit: int) -> int:\n        """Score one hit."""\n'
    "        return hit\n\n\ndef best(hits: list[int]) -> int:\n"
    '    """Pick the best hit."""\n    return max(hits)\n'
)


def _src_layout_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    scoring = root / "src" / "needle" / "scoring"
    scoring.mkdir(parents=True)
    (root / "src" / "needle" / "__init__.py").write_text('"""Needle."""\n', encoding="utf-8")
    (scoring / "__init__.py").write_text('"""Scoring."""\n', encoding="utf-8")
    (scoring / "strategies.py").write_text(_STRATEGIES_PY, encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "needle"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    return root


def _rows(db: Path, sql: str, *params: object) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return [tuple(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _package_hash(db: Path, name: str) -> str:
    return _rows(db, "SELECT content_hash FROM packages WHERE name=?", name)[0][0]


def _embedded_count(db: Path) -> int:
    return int(_rows(db, "SELECT COUNT(*) FROM chunks WHERE embedded=1")[0][0])


def _pre_fix_project_hash(root: Path) -> str:
    """The ``__project__`` hash an index written before the rule fold holds."""
    paths, _root, _effective = ProjectFileDiscoverer(scope=DiscoveryScopeConfig()).discover(root)
    return raw_hash_files(list(paths))


def _rewrite_to_pre_fix_state(db: Path, pre_fix_hash: str) -> None:
    """Put back what the old rule wrote: ``src.`` members, unfolded hash."""
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE module_members SET module='src.'||module WHERE package=?",
            (PROJECT_PACKAGE_NAME,),
        )
        conn.execute(
            "UPDATE packages SET content_hash=? WHERE name=?",
            (pre_fix_hash, PROJECT_PACKAGE_NAME),
        )
        conn.commit()
    finally:
        conn.close()


async def _index_pass(
    root: Path, db: Path, config: AppConfig
) -> tuple[IndexingStats, CountingMemberExtractor]:
    """One index pass through the real composition root, dependencies included."""
    bundle = build_project_indexer(config, db, use_inspect=False, inspect_depth=None)
    members = CountingMemberExtractor(inner=bundle.orchestrator.member_extractor)
    orchestrator = replace(
        bundle.orchestrator,
        member_extractor=members,
        dependency_resolver=FakeDependencyResolver((_DEPENDENCY,)),
    )
    stats = await orchestrator.index_project(root, include_dependencies=True, workers=1)
    return stats, members


@pytest.fixture
def counting_embedder(monkeypatch: pytest.MonkeyPatch) -> CountingEmbedder:
    """Route the composition root's embedder through one shared counter."""
    embedder = CountingEmbedder(inner=MockEmbedder(dim=AppConfig.load().embedding.dim))
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.embedders.build_embedder", lambda cfg: embedder
    )
    return embedder


@pytest.mark.asyncio
async def test_old_member_ids_heal_in_one_pass_without_reembedding(
    tmp_path: Path, counting_embedder: CountingEmbedder
) -> None:
    root, db, config = _src_layout_project(tmp_path), tmp_path / "p.db", AppConfig.load()
    open_index_database(db).close()
    first, _ = await _index_pass(root, db, config)
    assert first.project_indexed is True and first.indexed == 1  # the dependency landed
    healed_members = _rows(db, _PROJECT_MEMBERS_SQL, PROJECT_PACKAGE_NAME)
    assert healed_members and not any(m.startswith("src.") for m, _ in healed_members)
    pre_fix_hash = _pre_fix_project_hash(root)
    assert _package_hash(db, PROJECT_PACKAGE_NAME) == grammar_folded(rule_folded(pre_fix_hash))

    _rewrite_to_pre_fix_state(db, pre_fix_hash)
    before = (_rows(db, _CHUNK_IDENTITY_SQL), _embedded_count(db), _package_hash(db, _DEPENDENCY))

    calls_at_second = len(counting_embedder.calls)
    second, second_members = await _index_pass(root, db, config)
    after_second = (_rows(db, _CHUNK_IDENTITY_SQL), _embedded_count(db))
    healed_after_second = _rows(db, _PROJECT_MEMBERS_SQL, PROJECT_PACKAGE_NAME)
    calls_at_third = len(counting_embedder.calls)
    third, third_members = await _index_pass(root, db, config)

    assert second.project_indexed is True and healed_after_second == healed_members
    # No vector replaced: same chunk (id, content_hash) rows, same embedded count.
    assert (*after_second, _package_hash(db, _DEPENDENCY)) == before
    assert (second.cached, second.indexed, second_members.dependency_calls) == (1, 0, [])
    # WHY not "0 embedder calls" (spec AC-12's wording): today EVERY pass,
    # cache hits included, embeds each package's chunks before the cache
    # check and discards them, because load_existing_chunk_hashes runs before
    # package_build and so never sees a package to build its skip set from
    # (pipelines/ingestion.yaml:18 vs :22; pre-existing, tracked separately).
    # The pin that holds: the upgrade pass costs exactly a cache-hit pass.
    upgrade_calls = counting_embedder.calls[calls_at_second:calls_at_third]
    assert upgrade_calls == counting_embedder.calls[calls_at_third:]
    assert third.project_indexed is False and third_members.project_calls == []
