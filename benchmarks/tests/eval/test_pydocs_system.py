"""Pin PydocsMcpSystem: in-process index-then-search round-trip against a
synthetic project must surface a target symbol via the shipped chunk
pipeline. Also asserts teardown removes the temp SQLite file (so the
runner's per-system cleanup contract holds).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydocs_eval import _bench_cache
from pydocs_eval.registries import system_registry
from pydocs_eval.systems import PydocsMcpSystem
from pydocs_mcp.retrieval.config import AppConfig


@pytest.fixture
def _cache_off():
    # WHY: these tests pin the per-call *tmp* SQLite lifecycle (a fresh tmp
    # DB created on index, unlinked on teardown). That contract is the
    # cache-OFF path; with the bench cache enabled, index() reuses/keeps a
    # shared cached DB instead, so disable it here to assert tmp semantics.
    prior = _bench_cache.is_enabled()
    _bench_cache.set_enabled(False)
    yield
    _bench_cache.set_enabled(prior)


@pytest.mark.asyncio
async def test_index_then_search_returns_matching_chunk(tmp_path: Path, _cache_off) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(
        '''def widget_helper() -> int:
    """A helper that does widgety things."""
    return 42
'''
    )

    config = AppConfig.load()
    system = system_registry.build("pydocs-mcp")

    try:
        await system.index(tmp_path, config)
        items = await system.search("widget helper", limit=10)
    finally:
        await system.teardown()

    assert items, "pipeline returned no items"
    # WHY: the chunker tags the function chunk's title with the function
    # signature ("def widget_helper()") so a hit appears in qualified_name,
    # title metadata, or the chunk body itself — accept any of the three.
    haystack = " ".join(" ".join(filter(None, [it.qualified_name or "", it.text])) for it in items)
    assert "widget_helper" in haystack


@pytest.mark.asyncio
async def test_teardown_is_idempotent(tmp_path: Path, _cache_off) -> None:
    # WHY: the runner's failure path may call teardown twice (success +
    # finally cleanup). Idempotence keeps the second call from raising
    # and masking the original error.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")

    system = system_registry.build("pydocs-mcp")
    await system.index(tmp_path, AppConfig.load())

    db_path = system._db_path
    assert db_path is not None and db_path.exists()

    await system.teardown()
    assert not db_path.exists()
    # Second call must not raise even though the file is gone.
    await system.teardown()


@pytest.mark.asyncio
async def test_index_called_twice_does_not_leak_prior_db(tmp_path: Path, _cache_off) -> None:
    # WHY: a runner that re-uses one system instance across two corpora
    # (or recovers from a partial init failure by retrying ``index``)
    # would orphan the first tmp SQLite if ``index`` didn't clean prior
    # state. Pin: the first db file must be gone after the second
    # ``index()`` returns.
    corpus_a = tmp_path / "a"
    corpus_a.mkdir()
    (corpus_a / "__init__.py").write_text("")
    (corpus_a / "mod_a.py").write_text("def alpha() -> int:\n    return 1\n")

    corpus_b = tmp_path / "b"
    corpus_b.mkdir()
    (corpus_b / "__init__.py").write_text("")
    (corpus_b / "mod_b.py").write_text("def beta() -> int:\n    return 2\n")

    config = AppConfig.load()
    system = system_registry.build("pydocs-mcp")

    try:
        await system.index(corpus_a, config)
        first_db = system._db_path
        assert first_db is not None and first_db.exists()

        await system.index(corpus_b, config)
        second_db = system._db_path
        assert second_db is not None and second_db.exists()
        # WHY: identity check would be too strict — mkstemp may reuse the
        # same name on the same tick. What matters is the prior file path
        # no longer exists on disk after re-index.
        assert not first_db.exists() or first_db == second_db
    finally:
        await system.teardown()
