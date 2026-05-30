# benchmarks/tests/eval/test_bench_cache_fidelity.py
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.eval import _bench_cache
from benchmarks.eval.systems.pydocs import PydocsMcpSystem


def _tiny_corpus(tmp_path: Path) -> Path:
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "mod.py").write_text(
        "def alpha():\n    return 'alpha body'\n\n\ndef beta():\n    return 'beta body'\n"
    )
    (d / "pyproject.toml").write_text('[project]\nname="tiny"\nversion="0"\n')
    return d


async def _search_texts(corpus: Path) -> list[str]:
    from pydocs_mcp.retrieval.config import AppConfig

    system = PydocsMcpSystem()
    await system.index(corpus, AppConfig.load())
    hits = await system.search("alpha", limit=5)
    await system.teardown()
    return [h.text for h in hits]


async def test_cache_on_matches_cache_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    corpus = _tiny_corpus(tmp_path)

    _bench_cache.set_enabled(False)
    try:
        off = await _search_texts(corpus)
    finally:
        _bench_cache.set_enabled(True)

    on_cold = await _search_texts(corpus)  # builds cache
    on_warm = await _search_texts(corpus)  # cache hit

    assert off == on_cold == on_warm
    _bench_cache.set_enabled(True)
