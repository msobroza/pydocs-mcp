"""Concrete retrievers — replace the retrieval half of the deleted search.py."""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    SearchQuery,
    SearchScope,
)
from pydocs_mcp.retrieval.serialization import BuildContext, retriever_registry
from pydocs_mcp.storage.sqlite import _row_to_chunk, _row_to_module_member

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
    from pydocs_mcp.retrieval.protocols import ConnectionProvider


def _apply_scope(where: list[str], scope: SearchScope, column: str) -> None:
    if scope is SearchScope.PROJECT_ONLY:
        where.append(f"{column} = '__project__'")
    elif scope is SearchScope.DEPENDENCIES_ONLY:
        where.append(f"{column} != '__project__'")


@retriever_registry.register("bm25_chunk")
@dataclass(frozen=True, slots=True)
class Bm25ChunkRetriever:
    """BM25 FTS5 retriever over the `chunks` table."""

    provider: "ConnectionProvider"
    name: str = "bm25_chunk"

    async def retrieve(self, query: SearchQuery) -> ChunkList:
        return await asyncio.to_thread(self._retrieve_sync, query)

    def _retrieve_sync(self, query: SearchQuery) -> ChunkList:
        fts_ops = {"OR", "AND", "NOT"}
        tokens = query.terms.split()
        if any(t in fts_ops for t in tokens):
            fulltext = query.terms
        else:
            words = [w for w in tokens if len(w) > 1]
            if not words:
                return ChunkList(items=())
            fulltext = " OR ".join(f'"{w}"' for w in words)

        where = ["chunks_fts MATCH ?"]
        params: list = [fulltext]

        pf = query.pre_filter or {}
        package = pf.get(ChunkFilterField.PACKAGE.value)
        if package is not None:
            literal = package if package == "__project__" else normalize_package_name(package)
            where.append("c.package = ?")
            params.append(literal)

        scope_value = pf.get(ChunkFilterField.SCOPE.value)
        if scope_value is not None:
            _apply_scope(where, SearchScope(scope_value), "c.package")

        params.append(query.max_results)
        sql = (
            "SELECT c.id, c.package, c.title, c.text, c.origin, -m.rank AS rank "
            "FROM chunks_fts m JOIN chunks c ON c.id = m.rowid "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY rank LIMIT ?"
        )

        # Synchronous open inside the worker thread
        conn = sqlite3.connect(str(self.provider.cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.DatabaseError:
            return ChunkList(items=())
        finally:
            conn.close()

        items: list[Chunk] = []
        for row in rows:
            chunk = _row_to_chunk(row)
            items.append(
                Chunk(
                    text=chunk.text,
                    id=chunk.id,
                    relevance=float(row["rank"]),
                    retriever_name=self.name,
                    metadata=dict(chunk.metadata),  # unwrap MappingProxy for re-wrapping
                )
            )
        return ChunkList(items=tuple(items))

    def to_dict(self) -> dict:
        return {"type": "bm25_chunk"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "Bm25ChunkRetriever":
        return cls(provider=context.connection_provider)


@retriever_registry.register("like_member")
@dataclass(frozen=True, slots=True)
class LikeMemberRetriever:
    """LIKE retriever over `module_members.name` / `docstring`."""

    provider: "ConnectionProvider"
    name: str = "like_member"

    async def retrieve(self, query: SearchQuery) -> ModuleMemberList:
        return await asyncio.to_thread(self._retrieve_sync, query)

    def _retrieve_sync(self, query: SearchQuery) -> ModuleMemberList:
        escaped = (query.terms
                   .replace("\\", "\\\\")
                   .replace("%", "\\%")
                   .replace("_", "\\_"))
        pat = f"%{escaped}%"

        where = ["(lower(name) LIKE ? ESCAPE '\\' OR lower(docstring) LIKE ? ESCAPE '\\')"]
        params: list = [pat, pat]

        pf = query.pre_filter or {}
        package = pf.get(ModuleMemberFilterField.PACKAGE.value)
        if package is not None:
            literal = package if package == "__project__" else normalize_package_name(package)
            where.append("package = ?")
            params.append(literal)

        scope_value = pf.get(ChunkFilterField.SCOPE.value)
        if scope_value is not None:
            _apply_scope(where, SearchScope(scope_value), "package")

        params.append(query.max_results)
        sql = (
            "SELECT id, package, module, name, kind, signature, "
            "return_annotation, parameters, docstring "
            "FROM module_members "
            f"WHERE {' AND '.join(where)} "
            "LIMIT ?"
        )

        conn = sqlite3.connect(str(self.provider.cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.DatabaseError:
            return ModuleMemberList(items=())
        finally:
            conn.close()

        items: list[ModuleMember] = []
        for row in rows:
            member = _row_to_module_member(row)
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
        return {"type": "like_member"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "LikeMemberRetriever":
        return cls(provider=context.connection_provider)


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
