"""ChunkFetcherStep — candidate generation via SQLite FTS5 MATCH.

Single responsibility: take a query, return up to N candidate chunks
with FTS5's raw BM25 rank captured as ``relevance``. No score
normalization, no top-K cutoff, no rendering.

Pre-filter pushdown: when ``state.query.pre_filter`` is set,
:class:`~pydocs_mcp.retrieval.steps.pre_filter.PreFilterStep` MUST run
upstream and write a typed
:class:`~pydocs_mcp.retrieval.steps.pre_filter.PreFilterResult`
(``tree`` + ``scope``) to
``state.scratch["pre_filter.result"]``. The fetcher reads the parsed
tree, materializes it to the backend's query fragment via
:class:`pydocs_mcp.storage.protocols.FilterAdapter` (wired through
:attr:`BuildContext.filter_adapter`), and pushes the resulting WHERE
clause into the FTS5 JOIN. If the scratch key is missing while the
query carries a filter, the fetcher raises a clear ``RuntimeError``
pointing at the canonical YAML shape.

Mirrors the FTS5 SQL in :mod:`pydocs_mcp.storage.sqlite.SqliteLexicalStore`
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
from typing import TYPE_CHECKING, ClassVar

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    SearchScope,
)
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    step_registry,
    step_to_yaml_dict,
    yaml_kwargs,
)
from pydocs_mcp.retrieval.steps._sql_fetch import (
    execute_fetch,
    read_pre_filter_result,
    require_fetch_context,
)
from pydocs_mcp.storage.fts_query import build_fts_match_query as _build_fts_match_query

if TYPE_CHECKING:
    from pydocs_mcp.storage.protocols import FilterAdapter

# Deferred storage / filter_helpers imports: a top-level
# ``from pydocs_mcp.storage.filters import Filter`` (or
# ``from pydocs_mcp.retrieval.filter_helpers import _split_scope``)
# triggers a circular import via ``storage.__init__ → storage.sqlite →
# extraction → retrieval.config → retrieval.steps → this module``.
# Importing inside ``run`` resolves only at call time, by which point
# all retrieval/extraction modules have finished initializing.


# Mirror of ``SqliteLexicalStore.text_search`` — but emit RAW negative
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
    "SELECT c.id, c.package, c.module, c.title, c.text, c.origin, "
    "c.qualified_name, m.rank AS rank "
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

# Parameterizes the shared _sql_fetch error messages (byte-identical to the
# pre-extraction inline copies).
_STEP_LABEL = "ChunkFetcherStep"


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
    filter_adapter: FilterAdapter = field(kw_only=True)
    name: str = field(default="chunk_fetcher", kw_only=True)
    # WHY name is excluded: this step has never serialized ``name`` (parity-
    # pinned drift; unifying across steps is a follow-up).
    _YAML_KEYS: ClassVar[tuple[str, ...]] = ("limit", "retriever_name")

    async def run(self, state: RetrieverState) -> RetrieverState:
        fulltext = _build_fts_match_query(state.query.terms)
        if fulltext is None:
            return replace(state, candidates=ChunkList(items=()))

        result = read_pre_filter_result(
            state,
            step_label=_STEP_LABEL,
            step_name="chunk_fetcher",
            pipeline_yaml="pipelines/chunk_search.yaml",
        )
        filter_sql = ""
        filter_params: tuple = ()
        scope: frozenset[SearchScope] | None = None
        if result is not None:
            scope = result.scope
            if result.tree is not None:
                filter_sql, filter_params = self._build_where_clause(result.tree)

        where_parts = ["chunks_fts MATCH ?"]
        params: list = [fulltext]
        if filter_sql:
            where_parts.append(filter_sql)
            params.extend(filter_params)
        params.append(self.limit)
        sql = _FETCH_SQL_TEMPLATE.format(where=" AND ".join(where_parts))
        rows = await asyncio.to_thread(
            execute_fetch, self.provider, sql, params, step_label=_STEP_LABEL
        )
        chunks = tuple(_row_to_candidate(row, self.retriever_name) for row in rows)
        if scope is not None:
            # Lazy import — break the storage→extraction→retrieval.config→
            # retrieval.steps cycle (see module docstring).
            from pydocs_mcp.retrieval.filter_helpers import _matches_scope

            chunks = tuple(
                c
                for c in chunks
                if _matches_scope(c.metadata.get(ChunkFilterField.PACKAGE.value, ""), scope)
            )
        return replace(state, candidates=ChunkList(items=chunks))

    def _build_where_clause(self, tree) -> tuple[str, tuple]:
        """Materialize a parsed filter tree via the wired FilterAdapter.

        WHY: retrieval steps must never import the SQLite adapter at
        runtime — the composition root wires the concrete adapter into
        ``BuildContext.filter_adapter`` (see retrieval/factories.py).
        """
        return self.filter_adapter.adapt(tree, target_field="chunk")

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> ChunkFetcherStep:
        schema_name = data.get("schema_name", "chunk")
        app_config, provider = require_fetch_context(context, _STEP_LABEL)
        if context.filter_adapter is None:
            raise ValueError(
                "ChunkFetcherStep requires BuildContext.filter_adapter; "
                "the composition root wires SqliteFilterAdapter() "
                "(see retrieval/factories.py)."
            )
        allowed = frozenset(app_config.metadata_schemas[schema_name])
        return cls(
            provider=provider,
            allowed_fields=allowed,
            filter_adapter=context.filter_adapter,
            **yaml_kwargs(data, cls, cls._YAML_KEYS),
        )

    def to_dict(self) -> dict:
        return step_to_yaml_dict(self, type_name="chunk_fetcher", keys=self._YAML_KEYS)


def _row_to_candidate(row: sqlite3.Row, retriever_name: str) -> Chunk:
    """sqlite3.Row → Chunk with raw FTS5 rank captured as relevance.

    Mirrors :func:`pydocs_mcp.storage.sqlite.row_to_chunk` for metadata
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
    # qualified_name is a plain metadata key (not a ChunkFilterField) — the join
    # key the tree-rerank step uses to map BM25 candidates back to tree nodes.
    # Mirrors storage.sqlite.row_to_chunk; without it `rerank_candidates` can't
    # scope the tree and silently passes BM25 through.
    qname = row["qualified_name"]
    if qname:
        metadata["qualified_name"] = qname
    return Chunk(
        text=row["text"] or "",
        id=row["id"],
        relevance=float(row["rank"]),
        retriever_name=retriever_name,
        metadata=metadata,
    )


__all__ = ("ChunkFetcherStep",)
