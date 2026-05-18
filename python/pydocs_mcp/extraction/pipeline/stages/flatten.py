"""FlattenStage — fills ``state.chunks`` by walking each tree.

Thin wrapper — the walking / direct-text rule lives in
:mod:`pydocs_mcp.extraction.model.tree_flatten`; this stage just
concatenates per-tree results in pipeline order.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.extraction.model import flatten_to_chunks
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.pipeline.stages.base_stage import IngestionStage
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import Chunk


@stage_registry.register("flatten")
@dataclass(frozen=True, slots=True)
class FlattenStage(IngestionStage):
    name: str = "flatten"

    async def run(self, state: IngestionState) -> IngestionState:
        chunks = await asyncio.to_thread(self._flatten_all, state)
        return replace(state, chunks=tuple(chunks))

    def _flatten_all(self, state: IngestionState) -> list[Chunk]:
        out: list[Chunk] = []
        for tree in state.trees:
            out.extend(flatten_to_chunks(tree, package=state.package_name))
        return out

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "FlattenStage":
        return cls()

    def to_dict(self) -> dict:
        return {"type": "flatten"}


__all__ = ("FlattenStage",)
