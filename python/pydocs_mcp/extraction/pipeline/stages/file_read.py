"""FileReadStage — fills ``state.files.file_contents`` via parallel Rust read.

Wraps ``_fast.read_files_parallel`` under ``asyncio.to_thread`` — the
underlying Rayon iterator is CPU-bound on large projects, so offloading
keeps the event loop responsive.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry


@stage_registry.register("file_read")
@dataclass(frozen=True, slots=True)
class FileReadStage:
    name: str = "file_read"

    async def run(self, state: IngestionState) -> IngestionState:
        contents = await asyncio.to_thread(self._read, list(state.files.paths))
        new_files = replace(state.files, file_contents=tuple(contents))
        return replace(state, files=new_files)

    def _read(self, paths: list[str]) -> list[tuple[str, str]]:
        # Deferred so _fast's native/fallback choice is resolved lazily.
        from pydocs_mcp._fast import read_files_parallel
        return list(read_files_parallel(paths))

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> FileReadStage:
        return cls()

    def to_dict(self) -> dict:
        return {"type": "file_read"}


__all__ = ("FileReadStage",)
