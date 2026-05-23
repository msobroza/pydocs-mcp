"""ModuleMemberRetrievalStage — invoke the member retriever, store its result."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from pydocs_mcp.retrieval.pipeline_legacy import PipelineState
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.protocols import ModuleMemberRetriever


@stage_registry.register("module_member_retrieval")
@dataclass(frozen=True, slots=True)
class ModuleMemberRetrievalStage:
    retriever: "ModuleMemberRetriever"
    name: str = "module_member_retrieval"

    async def run(self, state: PipelineState) -> PipelineState:
        result = await self.retriever.retrieve(state.query)
        return replace(state, result=result)

    def to_dict(self) -> dict:
        return {"type": "module_member_retrieval", "retriever": self.retriever.to_dict()}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ModuleMemberRetrievalStage":
        return cls(retriever=context.retriever_registry.build(data["retriever"], context))


__all__ = ("ModuleMemberRetrievalStage",)
