# benchmarks/tests/eval/test_bench_cache_integration.py
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.eval import _bench_cache
from benchmarks.eval.systems.pydocs import PydocsMcpSystem


def _tiny_corpus(tmp_path: Path) -> Path:
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "mod.py").write_text("def hello():\n    return 1\n")
    (d / "pyproject.toml").write_text('[project]\nname="tiny"\nversion="0"\n')
    return d


@pytest.fixture
def _cache_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    prior = _bench_cache.is_enabled()
    _bench_cache.set_enabled(True)
    yield
    _bench_cache.set_enabled(prior)


async def test_index_once_reused_across_instances(tmp_path, monkeypatch, _cache_in_tmp) -> None:
    from pydocs_mcp.retrieval.config import AppConfig

    corpus = _tiny_corpus(tmp_path)
    config = AppConfig.load()

    calls = {"n": 0}
    real_do_index = PydocsMcpSystem._do_index

    async def counting_do_index(self, corpus_dir, cfg):
        calls["n"] += 1
        await real_do_index(self, corpus_dir, cfg)

    monkeypatch.setattr(PydocsMcpSystem, "_do_index", counting_do_index)

    a = PydocsMcpSystem()
    await a.index(corpus, config)
    await a.teardown()

    b = PydocsMcpSystem()
    await b.index(corpus, config)  # same (corpus, ingestion hash) -> cache hit
    await b.teardown()

    assert calls["n"] == 1  # indexed once, second was a cache hit


async def test_cache_off_indexes_every_time(tmp_path, monkeypatch, _cache_in_tmp) -> None:
    from pydocs_mcp.retrieval.config import AppConfig

    _bench_cache.set_enabled(False)
    corpus = _tiny_corpus(tmp_path)
    config = AppConfig.load()

    calls = {"n": 0}
    real_do_index = PydocsMcpSystem._do_index

    async def counting_do_index(self, corpus_dir, cfg):
        calls["n"] += 1
        await real_do_index(self, corpus_dir, cfg)

    monkeypatch.setattr(PydocsMcpSystem, "_do_index", counting_do_index)

    for _ in range(2):
        s = PydocsMcpSystem()
        await s.index(corpus, config)
        await s.teardown()

    assert calls["n"] == 2  # no cache -> indexed each time


async def test_teardown_keeps_cached_db(tmp_path, monkeypatch, _cache_in_tmp) -> None:
    from pydocs_mcp.retrieval.config import AppConfig

    corpus = _tiny_corpus(tmp_path)
    config = AppConfig.load()
    s = PydocsMcpSystem()
    await s.index(corpus, config)
    key = _bench_cache.make_key(corpus, config)
    cached_db = _bench_cache.db_path_for(key)
    assert cached_db.is_file()
    await s.teardown()
    assert cached_db.is_file()  # teardown must NOT delete the cache


async def test_failed_cold_index_leaves_no_orphan_build_dir(
    tmp_path, monkeypatch, _cache_in_tmp
) -> None:
    # AC16 / review C1: if _do_index raises on a MISS, the half-built
    # <key>.<pid>.tmp/ dir must not survive, and no entry is promoted.
    from pydocs_mcp.retrieval.config import AppConfig

    corpus = _tiny_corpus(tmp_path)
    config = AppConfig.load()

    async def boom(self, corpus_dir, cfg):
        raise RuntimeError("indexing blew up")

    monkeypatch.setattr(PydocsMcpSystem, "_do_index", boom)

    s = PydocsMcpSystem()
    with pytest.raises(RuntimeError, match="blew up"):
        await s.index(corpus, config)
    await s.teardown()  # must be safe even after the failed index

    root = _bench_cache.cache_root()
    leftovers = list(root.iterdir()) if root.exists() else []
    assert leftovers == []  # no .tmp build dir, no promoted entry
