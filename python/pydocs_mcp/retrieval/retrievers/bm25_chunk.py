"""Bm25ChunkRetriever — delegates text search to a ``TextSearchable`` store.

``pre_filter`` is parsed through the configured ``MetadataFilterFormat``
and validated against ``allowed_fields`` (sourced from
``AppConfig.metadata_schemas[schema_name]``). The resolved Filter tree
is pushed down to ``store.text_search(filter=...)`` for backend-side
enforcement — no post-retrieval pruning happens here (spec §5.7), with
the sole exception of ``scope`` which is split out of the pushdown
filter and re-applied in-process (see ``_shared._split_scope``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.models import ChunkFilterField, ChunkList, SearchQuery, SearchScope
from pydocs_mcp.retrieval.retrievers._shared import (
    _matches_scope,
    _schema_from_fields,
    _split_scope,
)
from pydocs_mcp.retrieval.serialization import BuildContext, retriever_registry
from pydocs_mcp.storage.filters import Filter, format_registry

if TYPE_CHECKING:
    from pydocs_mcp.storage.protocols import TextSearchable


@retriever_registry.register("bm25_chunk")
@dataclass(frozen=True, slots=True)
class Bm25ChunkRetriever:
    store: "TextSearchable"
    allowed_fields: frozenset[str]
    name: str = "bm25_chunk"
    schema_name: str = "chunk"

    async def retrieve(self, query: SearchQuery) -> ChunkList:
        tree: Filter | None = None
        scope: frozenset[SearchScope] | None = None
        if query.pre_filter is not None:
            tree = format_registry[query.pre_filter_format].parse(query.pre_filter)
            _schema_from_fields(self.allowed_fields).validate(tree)
            tree, scope = _split_scope(tree)
        results = await self.store.text_search(
            query_terms=query.terms,
            limit=query.max_results,
            filter=tree,
        )
        if scope is not None:
            results = tuple(
                r for r in results
                if _matches_scope(r.metadata.get(ChunkFilterField.PACKAGE.value, ""), scope)
            )
        return ChunkList(items=tuple(results))

    def to_dict(self) -> dict:
        return {"type": "bm25_chunk", "schema_name": self.schema_name}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "Bm25ChunkRetriever":
        schema_name = data.get("schema_name", "chunk")
        app_config = context.app_config
        if app_config is None:
            raise ValueError(
                "Bm25ChunkRetriever requires BuildContext.app_config; "
                "provide AppConfig at server/CLI startup."
            )
        vector_store = context.vector_store
        if vector_store is None:
            raise ValueError(
                "Bm25ChunkRetriever requires BuildContext.vector_store; "
                "provide SqliteVectorStore at server/CLI startup."
            )
        allowed = frozenset(app_config.metadata_schemas[schema_name])
        return cls(store=vector_store, allowed_fields=allowed, schema_name=schema_name)


__all__ = ("Bm25ChunkRetriever",)
