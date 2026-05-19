"""PipelineChunkRetriever — exposes a pipeline that yields ChunkList as a retriever."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.models import ChunkList, SearchQuery
from pydocs_mcp.retrieval.serialization import BuildContext, retriever_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline


@retriever_registry.register("pipeline_chunk")
@dataclass(frozen=True, slots=True)
class PipelineChunkRetriever:
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


__all__ = ("PipelineChunkRetriever",)
