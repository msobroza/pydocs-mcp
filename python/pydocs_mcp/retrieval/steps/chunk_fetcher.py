"""ChunkFetcherStep — candidate generation via SQLite FTS5 MATCH.

Single responsibility: take a query, return up to N candidate chunks
with FTS5's raw BM25 rank captured as ``relevance``. No score
normalization, no top-K cutoff, no rendering.

Pre-filter pushdown (Task 8): when ``state.query.pre_filter`` is set,
the filter tree is parsed through the configured
``MetadataFilterFormat``, validated against the schema's allowed
fields, and pushed into the SQL ``WHERE`` clause through the same
:class:`SqliteFilterAdapter` that ``SqliteVectorStore.text_search``
uses — so AC17 SQL parity is preserved. The semantic ``scope`` field
is split out via ``_split_scope`` and re-applied in-process via
``_matches_scope`` (the SQL adapter rejects ``scope`` as an unsafe
column, mirroring the legacy ``Bm25ChunkRetriever`` flow).

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

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    SearchScope,
)
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry

# Deferred storage / filter_helpers imports: a top-level
# ``from pydocs_mcp.storage.filters import Filter`` (or
# ``from pydocs_mcp.retrieval.filter_helpers import _split_scope``)
# triggers a circular import via ``storage.__init__ → storage.sqlite →
# extraction → retrieval.config → retrieval.steps → this module``.
# Importing inside ``run`` resolves only at call time, by which point
# all retrieval/extraction modules have finished initializing.

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


# Mirror of ``SqliteVectorStore.text_search`` — but emit RAW negative
# ``m.rank`` (no sign flip). The legacy storage layer flipped the sign
# in SQL (``-m.rank AS rank``), but the Task-4 refactor splits "fetch
# raw FTS5 ranks" from "normalize them to positive scores". The latter
# is :class:`BM25ScorerStep`'s job — keeping the responsibilities split
# lets a future :class:`DenseScorerStep` compose without touching fetch.
# Final relevance values post-scoring are identical (AC17 hash).
#
# ``ORDER BY m.rank`` (ascending) keeps the lowest-magnitude-negative
# ranks first — i.e., most relevant first — matching the legacy ORDER
# BY of ``-m.rank`` (descending) since the data is the same magnitudes.
_FETCH_SQL_TEMPLATE = (
    "SELECT c.id, c.package, c.module, c.title, c.text, c.origin, m.rank AS rank "
    "FROM chunks_fts m JOIN chunks c ON c.id = m.rowid "
    "WHERE {where} "
    "ORDER BY m.rank LIMIT ?"
)

# WHY: single source of truth for the fetch-side defaults. Referenced from
# the dataclass field defaults + to_dict (omit-when-default) + from_dict
# (fallback when YAML omits the key). Bumping a default touches one line,
# not three.
_DEFAULT_LIMIT = 50
_DEFAULT_RETRIEVER_NAME = "bm25_chunk"


@step_registry.register("chunk_fetcher")
@dataclass(frozen=True, slots=True)
class ChunkFetcherStep(RetrieverStep):
    """Candidate generation step for chunk pipelines.

    Reads ``state.query.terms`` (FTS MATCH) and ``state.query.pre_filter``
    (SQL pushdown). Writes ``state.candidates`` as a :class:`ChunkList`
    with ``relevance`` set to the RAW (negative) FTS5 BM25 rank.
    :class:`BM25ScorerStep` flips the sign downstream so the rest of the
    pipeline sorts "higher = better"; the final pipeline output matches
    the legacy storage-layer-flipped values (AC17 byte-parity).
    """

    provider: ConnectionProvider
    allowed_fields: frozenset[str] = field(default=frozenset(), kw_only=True)
    limit: int = field(default=_DEFAULT_LIMIT, kw_only=True)
    retriever_name: str = field(default=_DEFAULT_RETRIEVER_NAME, kw_only=True)
    name: str = field(default="chunk_fetcher", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        fulltext = _build_fts_match_query(state.query.terms)
        if fulltext is None:
            return replace(state, candidates=ChunkList(items=()))

        # Pre-filter pushdown: parse + validate + split scope. The SQL
        # branch carries the non-scope clauses; the scope set is applied
        # in-process below. Lazy imports break the storage→extraction→
        # retrieval.config→retrieval.steps cycle (see module docstring).
        from pydocs_mcp.retrieval.filter_helpers import (
            _matches_scope,
            _schema_from_fields,
            _split_scope,
        )
        from pydocs_mcp.storage.filters import format_registry

        tree = None
        scope: frozenset[SearchScope] | None = None
        if state.query.pre_filter is not None:
            tree = format_registry[state.query.pre_filter_format].parse(state.query.pre_filter)
            _schema_from_fields(self.allowed_fields).validate(tree)
            tree, scope = _split_scope(tree)

        filter_sql = ""
        filter_params: list = []
        if tree is not None:
            from pydocs_mcp.storage.sqlite import (
                _CHUNK_COLUMNS,
                SqliteFilterAdapter,
            )
            adapter = SqliteFilterAdapter(
                safe_columns=_CHUNK_COLUMNS, column_prefix="c.",
            )
            filter_sql, filter_params = adapter.adapt(tree)

        rows = await asyncio.to_thread(
            self._fetch_sync, fulltext, filter_sql, filter_params,
        )
        chunks = tuple(
            _row_to_candidate(row, self.retriever_name) for row in rows
        )
        if scope is not None:
            chunks = tuple(
                c for c in chunks
                if _matches_scope(c.metadata.get(ChunkFilterField.PACKAGE.value, ""), scope)
            )
        return replace(state, candidates=ChunkList(items=chunks))

    def _fetch_sync(
        self, fulltext: str, filter_sql: str, filter_params: list,
    ) -> list[sqlite3.Row]:
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
        where_parts = ["chunks_fts MATCH ?"]
        params: list = [fulltext]
        if filter_sql:
            where_parts.append(filter_sql)
            params.extend(filter_params)
        params.append(self.limit)
        sql = _FETCH_SQL_TEMPLATE.format(where=" AND ".join(where_parts))
        conn = sqlite3.connect(str(cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(sql, params).fetchall())
        finally:
            conn.close()

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ChunkFetcherStep":
        schema_name = data.get("schema_name", "chunk")
        if context.app_config is None:
            raise ValueError(
                "ChunkFetcherStep requires BuildContext.app_config; "
                "provide AppConfig at server/CLI startup."
            )
        allowed = frozenset(context.app_config.metadata_schemas[schema_name])
        return cls(
            provider=context.connection_provider,
            allowed_fields=allowed,
            limit=data.get("limit", _DEFAULT_LIMIT),
            retriever_name=data.get("retriever_name", _DEFAULT_RETRIEVER_NAME),
        )

    def to_dict(self) -> dict:
        d: dict = {"type": "chunk_fetcher"}
        if self.limit != _DEFAULT_LIMIT:
            d["limit"] = self.limit
        if self.retriever_name != _DEFAULT_RETRIEVER_NAME:
            d["retriever_name"] = self.retriever_name
        return d


def _row_to_candidate(row: sqlite3.Row, retriever_name: str) -> Chunk:
    """sqlite3.Row → Chunk with raw FTS5 rank captured as relevance.

    Mirrors :func:`pydocs_mcp.storage.sqlite._row_to_chunk` for metadata
    population. ``rank`` here is the RAW (negative) FTS5 rank; the sign
    flip happens in :class:`BM25ScorerStep` downstream. ``retriever_name``
    carries provenance so downstream parallel/RRF merges can trace which
    fetcher produced each candidate.
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
        retriever_name=retriever_name,
        metadata=metadata,
    )


__all__ = ("ChunkFetcherStep",)
