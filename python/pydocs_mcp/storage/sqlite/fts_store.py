"""SqliteLexicalStore — retrieval-only FTS5 (BM25) leg over ``chunks_fts``.

CRUD lives in :class:`~pydocs_mcp.storage.sqlite.chunk_repository.SqliteChunkRepository`;
this module only answers ``text_search`` (the ``TextSearchable`` view).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydocs_mcp.filters import Filter
from pydocs_mcp.models import Chunk
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.fts_query import build_fts_match_query
from pydocs_mcp.storage.sqlite.filter_adapter import (
    CHUNK_COLUMNS,
    _SqliteFilterTranslator,
)
from pydocs_mcp.storage.sqlite.row_mappers import row_to_chunk
from pydocs_mcp.storage.sqlite.table_crud import _resolve_filter
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire


@dataclass(frozen=True, slots=True)
class SqliteLexicalStore:
    """Retrieval-only lexical service over ``chunks_fts`` (BM25 / FTS5).

    CRUD happens via :class:`SqliteChunkRepository`; this type only answers
    ``text_search`` — it is the :class:`TextSearchable` view. Dense vector
    search lives behind ``SqliteCompositeBackend.dense()``
    (``storage/search_backend.py``).

    The default ``filter_adapter`` uses ``column_prefix="c."`` so filters
    produce qualified SQL for the ``chunks_fts m JOIN chunks c ON c.id = m.rowid``
    shape — ``chunks_fts`` shares column names with ``chunks`` and unqualified
    references would be ambiguous.
    """

    provider: ConnectionProvider
    filter_adapter: _SqliteFilterTranslator = field(
        default_factory=lambda: _SqliteFilterTranslator(
            safe_columns=CHUNK_COLUMNS,
            column_prefix="c.",
        )
    )
    retriever_name: str = "bm25_chunk"

    async def text_search(
        self,
        query_terms: str,
        limit: int,
        filter: Filter | Mapping | None = None,
    ) -> tuple[Chunk, ...]:
        tree = _resolve_filter(filter)
        # Validate/adapt filter before touching FTS — a bad column must raise
        # ValueError even when the query is empty.
        filter_sql, filter_params = "", []
        if tree is not None:
            filter_sql, filter_params = self.filter_adapter.adapt(tree)

        fulltext = build_fts_match_query(query_terms)
        if fulltext is None:
            return ()

        where_parts = ["chunks_fts MATCH ?"]
        params: list = [fulltext]
        if filter_sql:
            where_parts.append(filter_sql)
            params.extend(filter_params)
        params.append(limit)

        # ``c.decision_id`` rides along so ``row_to_chunk`` can hydrate the §D9
        # backlink on decision-as-chunk BM25 hits — get_why ranks these chunks
        # and needs the id to fetch the source record.
        sql = (
            "SELECT c.id, c.package, c.module, c.title, c.text, c.origin, "
            "c.content_hash, c.qualified_name, c.decision_id, -m.rank AS rank "
            "FROM chunks_fts m JOIN chunks c ON c.id = m.rowid "
            f"WHERE {' AND '.join(where_parts)} "
            "ORDER BY rank LIMIT ?"
        )

        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())

        items: list[Chunk] = []
        for row in rows:
            base = row_to_chunk(row)
            items.append(
                Chunk(
                    text=base.text,
                    id=base.id,
                    relevance=float(row["rank"]),
                    retriever_name=self.retriever_name,
                    metadata=dict(base.metadata),
                    content_hash=base.content_hash,  # defense-in-depth: don't trigger auto-compute
                )
            )
        return tuple(items)
