"""DenseScorerStep query-text normalization parity with DenseFetcherStep (W4).

``DenseFetcherStep`` embeds ``state.query.terms.strip()`` and short-circuits
on whitespace-only terms; ``DenseScorerStep`` historically embedded the raw
``state.query.terms``. Through real ``SearchQuery`` objects the two coincide
(its validator strips at construction, models.py), so the asymmetry is
latent — but a query-embedding cache keys on the exact text handed to
``embed_query``, which would split one logical query into two cache keys.
These tests pin the alignment: the scorer embeds the STRIPPED text and skips
scoring entirely (no embed, no store call, candidates untouched) when the
terms are whitespace-only.

Test-setup note: a real ``SearchQuery`` cannot hold unstripped or
whitespace-only terms (its validator strips and rejects empties), so these
tests build ``RetrieverState`` with a stub query object — ``query`` is an
unvalidated dataclass slot (retrieval/pipeline/state.py).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.dense_scorer import DenseScorerStep

_DIM = 8


@dataclass
class _StubQuery:
    """Minimal query stub — bypasses SearchQuery's construction-time strip."""

    terms: str


class _SpyEmbedder:
    """Records every embed_query text so tests can assert what was embedded."""

    dim: int = _DIM
    model_name: str = "spy"

    def __init__(self) -> None:
        self.query_calls: list[str] = []

    async def embed_query(self, text: str) -> np.ndarray:
        self.query_calls.append(text)
        return np.ones(_DIM, dtype=np.float32)

    async def embed_chunks(self, texts) -> tuple[np.ndarray, ...]:
        return tuple(np.ones(_DIM, dtype=np.float32) for _ in texts)


class _SpyStore:
    """VectorScoreable double — echoes the subset back with fixed scores."""

    def __init__(self) -> None:
        self.score_calls: list[list[int]] = []

    async def score(self, query_vector, *, subset_chunk_ids, top_k):
        self.score_calls.append(list(subset_chunk_ids))
        return [(cid, 0.5) for cid in subset_chunk_ids]


def _state(terms: str) -> RetrieverState:
    chunk = Chunk(text="body", metadata={"package": "p", "title": "t"}, id=1)
    return RetrieverState(
        query=_StubQuery(terms=terms),  # type: ignore[arg-type]
        candidates=ChunkList(items=(chunk,)),
    )


@pytest.mark.asyncio
async def test_dense_scorer_embeds_stripped_terms() -> None:
    embedder = _SpyEmbedder()
    step = DenseScorerStep(store=_SpyStore(), embedder=embedder, top_k=10)

    await step.run(_state("  batch inference \n"))

    assert embedder.query_calls == ["batch inference"], (
        "dense_scorer must embed terms.strip() — the unstripped text splits "
        "one logical query into two query-cache keys (W4)"
    )


@pytest.mark.asyncio
async def test_dense_scorer_skips_scoring_on_whitespace_only_terms() -> None:
    embedder = _SpyEmbedder()
    store = _SpyStore()
    step = DenseScorerStep(store=store, embedder=embedder, top_k=10)
    state = _state("   \n\t")

    out = await step.run(state)

    assert embedder.query_calls == [], "whitespace-only terms must not be embedded"
    assert store.score_calls == [], "whitespace-only terms must not hit the store"
    assert out.candidates is state.candidates, (
        "skip-scoring must be a pass-through: candidates untouched, incoming "
        "order preserved (mirrors DenseFetcherStep's empty-terms guard)"
    )
