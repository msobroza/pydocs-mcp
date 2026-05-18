"""PipelineModuleMemberRetriever — exposes a pipeline that yields ModuleMemberList."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.models import ModuleMemberList, SearchQuery
from pydocs_mcp.retrieval.serialization import BuildContext, retriever_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline


@retriever_registry.register("pipeline_member")
@dataclass(frozen=True, slots=True)
class PipelineModuleMemberRetriever:
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


__all__ = ("PipelineModuleMemberRetriever",)
