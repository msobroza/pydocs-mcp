"""ChunkingStage — fills ``state.trees`` by dispatching each file to a chunker.

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
    chunking_config: "ChunkingConfig"
    name: str = "chunking"

    async def run(self, state: IngestionState) -> IngestionState:
        trees = await asyncio.to_thread(self._chunk_all, state)
        # I7 commit 2 — write trees to ChunkBundle AND mirror to legacy
        # flat field. Commit 3 drops the flat duplicate.
        trees_tuple = tuple(trees)
        new_chunks_bundle = replace(state.chunks_bundle, trees=trees_tuple)
        return replace(state, chunks_bundle=new_chunks_bundle, trees=trees_tuple)

    def _chunk_all(self, state: IngestionState) -> list[DocumentNode]:
        trees: list[DocumentNode] = []
        # I7 commit 2 — read file_contents from the FileBundle, fall back
        # to the legacy flat field for callers that haven't migrated.
        file_contents = (
            state.files.file_contents if state.files.file_contents
            else state.file_contents
        )
        for path, source in file_contents:
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
        # I7 commit 2 — read package_name + root from FileBundle, fall back
        # to the legacy flat fields if the bundle isn't populated. The
        # default :class:`FileBundle` ``root`` is ``Path(".")`` (same as the
        # legacy state default), so checking for that sentinel selects the
        # caller-supplied flat field when present.
        package_name = state.files.package_name or state.package_name
        root = state.files.root if state.files.root != Path(".") else state.root
        try:
            return chunker.build_tree(path, source, package_name, root)
        except Exception as exc:  # noqa: BLE001 -- AC #27: per-file failure must not abort pipeline
            log.warning("chunker %s failed on %s: %s", ext, path, exc)
            return None

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "ChunkingStage":
        return cls(chunking_config=context.app_config.extraction.chunking)

    def to_dict(self) -> dict:
        return {"type": "chunking"}


__all__ = ("ChunkingStage",)
