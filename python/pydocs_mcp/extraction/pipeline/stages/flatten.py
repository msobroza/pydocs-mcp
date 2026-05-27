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
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import Chunk


@stage_registry.register("flatten")
@dataclass(frozen=True, slots=True)
class FlattenStage:
    name: str = "flatten"

    async def run(self, state: IngestionState) -> IngestionState:
        chunks = await asyncio.to_thread(self._flatten_all, state)
        # I7 commit 2 — write chunks to ChunkBundle AND mirror to legacy
        # flat field. Commit 3 drops the flat duplicate.
        chunks_tuple = tuple(chunks)
        new_chunks_bundle = replace(state.chunks_bundle, chunks=chunks_tuple)
        return replace(state, chunks_bundle=new_chunks_bundle, chunks=chunks_tuple)

    def _flatten_all(self, state: IngestionState) -> list[Chunk]:
        out: list[Chunk] = []
        # I7 commit 2 — read trees from ChunkBundle, fall back to flat;
        # read package_name from FileBundle, fall back to flat.
        trees = state.chunks_bundle.trees if state.chunks_bundle.trees else state.trees
        package_name = state.files.package_name or state.package_name
        for tree in trees:
            out.extend(flatten_to_chunks(tree, package=package_name))
        return out

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "FlattenStage":
        return cls()

    def to_dict(self) -> dict:
        return {"type": "flatten"}


__all__ = ("FlattenStage",)
