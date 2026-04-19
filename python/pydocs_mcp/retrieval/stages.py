"""Pipeline stages — spec §5.6 (12 classes). Part 1: retrieval + filters + limit."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from pydocs_mcp.models import (
    ChunkFilterField,
    ChunkList,
    ModuleMemberList,
    PipelineResultItem,
    SearchScope,
)
from pydocs_mcp.retrieval.pipeline import PipelineState
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.protocols import (
        ChunkRetriever,
        ModuleMemberRetriever,
    )


# Retrieval stages


@stage_registry.register("chunk_retrieval")
@dataclass(frozen=True, slots=True)
class ChunkRetrievalStage:
    retriever: "ChunkRetriever"
    name: str = "chunk_retrieval"

    async def run(self, state: PipelineState) -> PipelineState:
        result = await self.retriever.retrieve(state.query)
        return replace(state, result=result)

    def to_dict(self) -> dict:
        return {"type": "chunk_retrieval", "retriever": self.retriever.to_dict()}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ChunkRetrievalStage":
        return cls(retriever=context.retriever_registry.build(data["retriever"], context))


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


# Filter stages


def _filter_result_items(result: PipelineResultItem | None, predicate) -> PipelineResultItem | None:
    if result is None:
        return None
    if isinstance(result, ChunkList):
        return ChunkList(items=tuple(item for item in result.items if predicate(item)))
    # ModuleMemberList
    return ModuleMemberList(items=tuple(item for item in result.items if predicate(item)))


@stage_registry.register("package_filter")
@dataclass(frozen=True, slots=True)
class PackageFilterStage:
    name: str = "package_filter"

    async def run(self, state: PipelineState) -> PipelineState:
        target = (state.query.pre_filter or {}).get(ChunkFilterField.PACKAGE.value)
        if not target:
            return state
        def keep(item):
            return item.metadata.get(ChunkFilterField.PACKAGE.value) == target
        return replace(state, result=_filter_result_items(state.result, keep))

    def to_dict(self) -> dict:
        return {"type": "package_filter"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "PackageFilterStage":
        return cls()


@stage_registry.register("scope_filter")
@dataclass(frozen=True, slots=True)
class ScopeFilterStage:
    name: str = "scope_filter"

    async def run(self, state: PipelineState) -> PipelineState:
        raw = (state.query.pre_filter or {}).get(ChunkFilterField.SCOPE.value)
        if raw is None:
            return state
        scope = SearchScope(raw)
        def keep(item):
            package = item.metadata.get(ChunkFilterField.PACKAGE.value, "")
            if scope is SearchScope.PROJECT_ONLY:
                return package == "__project__"
            if scope is SearchScope.DEPENDENCIES_ONLY:
                return package != "__project__"
            return True
        return replace(state, result=_filter_result_items(state.result, keep))

    def to_dict(self) -> dict:
        return {"type": "scope_filter"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ScopeFilterStage":
        return cls()


@stage_registry.register("title_filter")
@dataclass(frozen=True, slots=True)
class TitleFilterStage:
    name: str = "title_filter"

    async def run(self, state: PipelineState) -> PipelineState:
        target = (state.query.pre_filter or {}).get(ChunkFilterField.TITLE.value)
        if not target:
            return state
        pattern = str(target).lower()
        def keep(item):
            title = (item.metadata.get(ChunkFilterField.TITLE.value, "") or "").lower()
            return pattern in title
        return replace(state, result=_filter_result_items(state.result, keep))

    def to_dict(self) -> dict:
        return {"type": "title_filter"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "TitleFilterStage":
        return cls()


@stage_registry.register("limit")
@dataclass(frozen=True, slots=True)
class LimitStage:
    max_results: int = 8
    name: str = "limit"

    async def run(self, state: PipelineState) -> PipelineState:
        if state.result is None:
            return state
        capped = state.result.items[: self.max_results]
        if isinstance(state.result, ChunkList):
            return replace(state, result=ChunkList(items=tuple(capped)))
        return replace(state, result=ModuleMemberList(items=tuple(capped)))

    def to_dict(self) -> dict:
        d: dict = {"type": "limit"}
        if self.max_results != 8:
            d["max_results"] = self.max_results
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "LimitStage":
        return cls(max_results=data.get("max_results", 8))
