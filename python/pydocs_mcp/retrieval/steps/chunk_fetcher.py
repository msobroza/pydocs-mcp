"""ChunkFetcherStep — candidate generation via SQLite FTS5 MATCH.

Single responsibility: take a query, return up to N candidate chunks
with FTS5's raw BM25 rank captured as ``relevance``. No score
normalization, no top-K cutoff, no rendering.

Mirrors the FTS5 SQL in :mod:`pydocs_mcp.storage.sqlite.SqliteVectorStore`
but deliberately does NOT flip the sign of FTS5's negative rank — that's
:class:`BM25ScorerStep`'s job in the next step. Splitting fetch from
score keeps each step single-responsibility and lets a future
``DenseScorerStep`` (PR-B3.1) compose alongside :class:`BM25ScorerStep`
without rewriting fetch logic.
"""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, field, replace

from pydocs_mcp.models import Chunk, ChunkFilterField, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.protocols import ConnectionProvider

# Build the FTS5 MATCH expression the same way SqliteVectorStore does so
# observable behavior matches: short tokens are dropped, "OR" joins
# remaining quoted terms, FTS5 operators in the raw input are preserved.
_FTS_OPS = frozenset({"AND", "OR", "NOT", "NEAR"})


def _build_fts_match_query(terms: str) -> str | None:
    """Shape raw user terms into an FTS5 MATCH expression.

    Mirror of :func:`pydocs_mcp.storage.sqlite._build_fts_match_query`.
    Kept in sync intentionally — when the storage layer's query shaping
    changes, this needs to change with it (parity verified by AC17 baseline).
    """
    tokens = terms.split()
    if any(t in _FTS_OPS for t in tokens):
        return terms
    words = [w for w in tokens if len(w) > 1]
    if not words:
        return None
    return " OR ".join(f'"{w}"' for w in words)


_FETCH_SQL = (
    "SELECT c.id, c.package, c.module, c.title, c.text, c.origin, m.rank AS rank "
    "FROM chunks_fts m JOIN chunks c ON c.id = m.rowid "
    "WHERE chunks_fts MATCH ? "
    "ORDER BY m.rank LIMIT ?"
)


@dataclass(frozen=True, slots=True)
class ChunkFetcherStep(RetrieverStep):
    """Candidate generation step for chunk pipelines.

    Reads ``state.query.terms``. Writes ``state.candidates`` as a
    :class:`ChunkList`. Each chunk's ``relevance`` is FTS5's raw negative
    BM25 rank (lower-magnitude-negative = better match). Sign is flipped
    by :class:`BM25ScorerStep` downstream so the rest of the pipeline can
    sort "higher = better".
    """

    provider: ConnectionProvider
    limit: int = field(default=50, kw_only=True)
    name: str = field(default="chunk_fetcher", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        fulltext = _build_fts_match_query(state.query.terms)
        if fulltext is None:
            return replace(state, candidates=ChunkList(items=()))
        rows = await asyncio.to_thread(self._fetch_sync, fulltext)
        chunks = tuple(_row_to_candidate(row) for row in rows)
        return replace(state, candidates=ChunkList(items=chunks))

    def _fetch_sync(self, fulltext: str) -> list[sqlite3.Row]:
        # WHY: PerCallConnectionProvider exposes ``cache_path`` directly so a
        # sync-friendly fresh connection avoids tangling with the provider's
        # async ``acquire()`` context manager from inside ``to_thread``.
        # Mirrors the connection-open code in PerCallConnectionProvider._open.
        cache_path = getattr(self.provider, "cache_path", None)
        if cache_path is None:
            raise TypeError(
                "ChunkFetcherStep requires a provider exposing 'cache_path'; "
                f"got {type(self.provider).__name__}"
            )
        conn = sqlite3.connect(str(cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(_FETCH_SQL, (fulltext, self.limit)).fetchall())
        finally:
            conn.close()


def _row_to_candidate(row: sqlite3.Row) -> Chunk:
    """sqlite3.Row → Chunk, with raw FTS5 rank as relevance.

    Mirrors :func:`pydocs_mcp.storage.sqlite._row_to_chunk` for metadata
    population, but additionally captures FTS5's raw negative bm25 rank
    in the ``relevance`` field. :class:`BM25ScorerStep` flips the sign.
    """
    metadata: dict[str, object] = {}
    for key in (
        ChunkFilterField.PACKAGE.value,
        ChunkFilterField.MODULE.value,
        ChunkFilterField.TITLE.value,
        ChunkFilterField.ORIGIN.value,
    ):
        value = row[key]
        if value:
            metadata[key] = value
    return Chunk(
        text=row["text"] or "",
        id=row["id"],
        relevance=float(row["rank"]),
        metadata=metadata,
    )


__all__ = ("ChunkFetcherStep",)
