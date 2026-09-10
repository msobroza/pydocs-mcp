"""Both dense query steps send the same prefixed text with the cache OFF.

``DenseFetcherStep`` and ``DenseScorerStep`` each call ``embed_query`` on the
serving chain. With ``query_cache.enabled: false`` nothing coalesces them, so
this pins that the provider sees one identical prefixed, normalized string
from both — the property that keeps cache-on and cache-off runs comparable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.factories import build_retrieval_context
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.dense_fetcher import DenseFetcherStep
from pydocs_mcp.retrieval.steps.dense_scorer import DenseScorerStep
from tests._fakes import RecordingEmbedder

_PREFIX = "Instruct: find the code\nQuery:"


@dataclass
class _StubQuery:
    """Bypasses SearchQuery's construction-time strip (see
    test_dense_scorer_strip_alignment.py) so the scorer-side normalization
    is observable."""

    terms: str


class _EchoScoreStore:
    """VectorScoreable double — echoes the subset back with fixed scores."""

    async def score(self, query_vector, *, subset_chunk_ids, top_k):
        return [(cid, 0.5) for cid in subset_chunk_ids]


def _scorer_state(terms: str) -> RetrieverState:
    chunk = Chunk(text="body", metadata={"package": "p", "title": "t"}, id=1)
    return RetrieverState(
        query=_StubQuery(terms=terms),  # type: ignore[arg-type]
        candidates=ChunkList(items=(chunk,)),
    )


async def test_fetcher_and_scorer_send_identical_prefixed_text_cache_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PYDOCS_EMBEDDING__QUERY_PREFIX", _PREFIX)
    monkeypatch.setenv("PYDOCS_EMBEDDING__QUERY_CACHE__ENABLED", "false")
    config = AppConfig.load()
    provider = RecordingEmbedder(dim=config.embedding.dim)
    monkeypatch.setattr("pydocs_mcp.retrieval.factories.build_embedder", lambda cfg: provider)
    db_path = tmp_path / "x.db"
    open_index_database(db_path).close()
    ctx = build_retrieval_context(db_path, config)
    assert ctx.embedder is not None
    fetcher = DenseFetcherStep.from_dict({}, ctx)
    scorer = DenseScorerStep(store=_EchoScoreStore(), embedder=ctx.embedder, top_k=5)
    await fetcher.run(RetrieverState(query=SearchQuery(terms="  q ", max_results=5)))
    await scorer.run(_scorer_state("  q \n"))
    assert provider.query_texts == [_PREFIX + "q", _PREFIX + "q"]
