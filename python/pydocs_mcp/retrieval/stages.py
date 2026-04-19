"""Pipeline stages — spec §5.6 (12 classes)."""
from __future__ import annotations

import asyncio
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
        PipelineStage,
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


# Composition stages


@stage_registry.register("parallel_retrieval")
@dataclass(frozen=True, slots=True)
class ParallelRetrievalStage:
    stages: tuple["PipelineStage", ...] = ()
    name: str = "parallel_retrieval"

    async def run(self, state: PipelineState) -> PipelineState:
        # Each inner stage sees the SAME input state independently; results concatenate.
        results = await asyncio.gather(*(s.run(state) for s in self.stages))
        # Concatenate the new items from each branch onto the initial state.result
        initial_items: tuple = ()
        if state.result is not None:
            initial_items = state.result.items

        accumulated_items: list = list(initial_items)
        first_type = type(state.result) if state.result is not None else None

        for branch_state in results:
            if branch_state.result is None:
                continue
            branch_type = type(branch_state.result)
            # Skip items that are already in accumulated_items (branch inherited the input)
            new_items = branch_state.result.items[len(initial_items):]
            accumulated_items.extend(new_items)
            if first_type is None:
                first_type = branch_type

        if first_type is ChunkList:
            return replace(state, result=ChunkList(items=tuple(accumulated_items)))
        if first_type is ModuleMemberList:
            return replace(state, result=ModuleMemberList(items=tuple(accumulated_items)))
        return state

    def to_dict(self) -> dict:
        return {"type": "parallel_retrieval", "stages": [s.to_dict() for s in self.stages]}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ParallelRetrievalStage":
        return cls(stages=tuple(context.stage_registry.build(s, context) for s in data["stages"]))


@stage_registry.register("reciprocal_rank_fusion")
@dataclass(frozen=True, slots=True)
class ReciprocalRankFusionStage:
    k: int = 60
    name: str = "reciprocal_rank_fusion"

    async def run(self, state: PipelineState) -> PipelineState:
        if state.result is None or not state.result.items:
            return state
        # Score by 1/(k+rank), keyed by item id (fall back to id(item))
        scores: dict = {}
        items_by_key: dict = {}
        for rank, item in enumerate(state.result.items):
            key = item.id if item.id is not None else id(item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (self.k + rank)
            items_by_key[key] = item

        # Rebuild ordered result, stable by score desc
        sorted_keys = sorted(scores.keys(), key=lambda k_: scores[k_], reverse=True)
        sorted_items = tuple(items_by_key[k_] for k_ in sorted_keys)
        if isinstance(state.result, ChunkList):
            return replace(state, result=ChunkList(items=sorted_items))
        return replace(state, result=ModuleMemberList(items=sorted_items))

    def to_dict(self) -> dict:
        d: dict = {"type": "reciprocal_rank_fusion"}
        if self.k != 60:
            d["k"] = self.k
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ReciprocalRankFusionStage":
        return cls(k=data.get("k", 60))
