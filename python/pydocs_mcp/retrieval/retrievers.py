"""Concrete retrievers — consume storage Protocols via BuildContext (spec §5.7)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.models import (
    ChunkList,
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    SearchQuery,
)
from pydocs_mcp.retrieval.serialization import BuildContext, retriever_registry
from pydocs_mcp.storage.filters import Filter, _walk_fields, format_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
    from pydocs_mcp.storage.protocols import TextSearchable
    from pydocs_mcp.storage.sqlite import SqliteModuleMemberRepository


@retriever_registry.register("bm25_chunk")
@dataclass(frozen=True, slots=True)
class Bm25ChunkRetriever:
    """BM25 retriever — delegates text search to a ``TextSearchable`` store.

    ``pre_filter`` is parsed through the configured ``MetadataFilterFormat``
    and validated against ``allowed_fields`` (sourced from
    ``AppConfig.metadata_schemas[schema_name]``). The resolved Filter tree is
    pushed down to ``store.text_search(filter=...)`` for backend-side
    enforcement — no post-retrieval pruning happens here (spec §5.7).
    """

    store: "TextSearchable"
    allowed_fields: frozenset[str]
    name: str = "bm25_chunk"
    schema_name: str = "chunk"

    async def retrieve(self, query: SearchQuery) -> ChunkList:
        tree: Filter | None = None
        if query.pre_filter is not None:
            tree = format_registry[query.pre_filter_format].parse(query.pre_filter)
            unknown = _walk_fields(tree) - self.allowed_fields
            if unknown:
                raise ValueError(
                    f"filter references unknown fields {sorted(unknown)}; "
                    f"retriever allows {sorted(self.allowed_fields)}"
                )
        results = await self.store.text_search(
            query_terms=query.terms,
            limit=query.max_results,
            filter=tree,
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


@retriever_registry.register("like_member")
@dataclass(frozen=True, slots=True)
class LikeMemberRetriever:
    """LIKE-based retriever over ``module_members``.

    ``pre_filter`` (e.g. ``{"package": "fastapi"}``) is parsed via
    ``format_registry`` and pushed down to ``store.list(filter=...)`` — the
    safe-column allowlist on :class:`SqliteModuleMemberRepository` rejects
    unknown columns before SQL is emitted. ``query.terms`` then filters the
    returned rows against ``name`` / ``docstring`` substrings in-process,
    since ``ModuleMemberStore`` offers no text-search contract yet (spec §5.7).
    """

    store: "SqliteModuleMemberRepository"
    allowed_fields: frozenset[str]
    name: str = "like_member"
    schema_name: str = "member"

    async def retrieve(self, query: SearchQuery) -> ModuleMemberList:
        tree: Filter | None = None
        if query.pre_filter is not None:
            tree = format_registry[query.pre_filter_format].parse(query.pre_filter)
            unknown = _walk_fields(tree) - self.allowed_fields
            if unknown:
                raise ValueError(
                    f"filter references unknown fields {sorted(unknown)}; "
                    f"retriever allows {sorted(self.allowed_fields)}"
                )
        rows = await self.store.list(filter=tree, limit=query.max_results)
        needle = query.terms.lower()
        items: list[ModuleMember] = []
        for member in rows:
            name_value = str(member.metadata.get(ModuleMemberFilterField.NAME.value, "")).lower()
            doc_value = str(member.metadata.get("docstring", "")).lower()
            if needle in name_value or needle in doc_value:
                items.append(
                    ModuleMember(
                        id=member.id,
                        relevance=None,
                        retriever_name=self.name,
                        metadata=dict(member.metadata),
                    )
                )
        return ModuleMemberList(items=tuple(items))

    def to_dict(self) -> dict:
        return {"type": "like_member", "schema_name": self.schema_name}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "LikeMemberRetriever":
        schema_name = data.get("schema_name", "member")
        app_config = context.app_config
        if app_config is None:
            raise ValueError(
                "LikeMemberRetriever requires BuildContext.app_config; "
                "provide AppConfig at server/CLI startup."
            )
        module_member_repo = context.module_member_store
        if module_member_repo is None:
            raise ValueError(
                "LikeMemberRetriever requires BuildContext.module_member_store; "
                "provide SqliteModuleMemberRepository at server/CLI startup."
            )
        allowed = frozenset(app_config.metadata_schemas[schema_name])
        return cls(store=module_member_repo, allowed_fields=allowed, schema_name=schema_name)


@retriever_registry.register("pipeline_chunk")
@dataclass(frozen=True, slots=True)
class PipelineChunkRetriever:
    """Adapter — exposes an inner pipeline that produces a ChunkList as a ChunkRetriever."""

    pipeline: "CodeRetrieverPipeline"
    name: str = "pipeline_chunk"

    async def retrieve(self, query: SearchQuery) -> ChunkList:
        state = await self.pipeline.run(query)
        if isinstance(state.result, ChunkList):
            return state.result
        return ChunkList(items=())

    def to_dict(self) -> dict:
        return {"type": "pipeline_chunk", "pipeline": self.pipeline.to_dict()}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "PipelineChunkRetriever":
        from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
        return cls(pipeline=CodeRetrieverPipeline.from_dict(data["pipeline"], context))


@retriever_registry.register("pipeline_member")
@dataclass(frozen=True, slots=True)
class PipelineModuleMemberRetriever:
    """Adapter — exposes an inner pipeline that produces a ModuleMemberList as a ModuleMemberRetriever."""

    pipeline: "CodeRetrieverPipeline"
    name: str = "pipeline_member"

    async def retrieve(self, query: SearchQuery) -> ModuleMemberList:
        state = await self.pipeline.run(query)
        if isinstance(state.result, ModuleMemberList):
            return state.result
        return ModuleMemberList(items=())

    def to_dict(self) -> dict:
        return {"type": "pipeline_member", "pipeline": self.pipeline.to_dict()}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "PipelineModuleMemberRetriever":
        from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
        return cls(pipeline=CodeRetrieverPipeline.from_dict(data["pipeline"], context))
