"""ChunkingStage — fills ``state.chunks.trees`` by dispatching each file to a chunker.

Per-file failures are isolated (spec AC #27): one broken file must not
abort ingestion of the whole project. Unknown extensions are dropped
silently — the dispatch policy is ``chunker_registry[ext]`` and missing
registrations are a wiring concern, not a per-run error.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pydocs_mcp.extraction.strategies.chunkers  # noqa: F401 — side-effect: fires @chunker_registry.register decorators
from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import chunker_registry, stage_registry

if TYPE_CHECKING:
    from pydocs_mcp.extraction.config import ChunkingConfig

log = logging.getLogger("pydocs-mcp")


@stage_registry.register("chunking")
@dataclass(frozen=True, slots=True)
class ChunkingStage:
    chunking_config: ChunkingConfig
    name: str = "chunking"

    async def run(self, state: IngestionState) -> IngestionState:
        trees = await asyncio.to_thread(self._chunk_all, state)
        new_chunks = replace(state.chunks, trees=tuple(trees))
        return replace(state, chunks=new_chunks)

    def _chunk_all(self, state: IngestionState) -> list[DocumentNode]:
        trees: list[DocumentNode] = []
        for path, source in state.files.file_contents:
            tree = self._chunk_one(path, source, state)
            if tree is not None:
                trees.append(tree)
        return trees

    def _chunk_one(
        self, path: str, source: str, state: IngestionState,
    ) -> DocumentNode | None:
        if not source:
            return None
        ext = Path(path).suffix.lower()
        chunker_cls = chunker_registry.get(ext)
        if chunker_cls is None:
            return None  # unknown extension — skip silently (policy, not error)
        chunker = chunker_cls.from_config(self.chunking_config)
        try:
            return chunker.build_tree(
                path, source, state.files.package_name, state.files.root,
            )
        except Exception as exc:
            log.warning("chunker %s failed on %s: %s", ext, path, exc)
            return None

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> ChunkingStage:
        return cls(chunking_config=context.app_config.extraction.chunking)

    def to_dict(self) -> dict:
        return {"type": "chunking"}


__all__ = ("ChunkingStage",)
