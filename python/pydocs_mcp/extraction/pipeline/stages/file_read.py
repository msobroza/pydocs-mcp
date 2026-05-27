"""FileReadStage — fills ``state.file_contents`` via parallel Rust read.

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
        # I7 commit 2 — read paths from the FileBundle (populated by
        # ``FileDiscoveryStage``); fall back to the legacy flat field for
        # tests / callers that haven't migrated. Write the result to both
        # the bundle AND the legacy flat field — the mirror write is
        # retained until commit 3 drops the flat field.
        paths = state.files.paths if state.files.paths else state.paths
        contents = await asyncio.to_thread(self._read, list(paths))
        contents_tuple = tuple(contents)
        new_files = replace(state.files, file_contents=contents_tuple)
        return replace(state, files=new_files, file_contents=contents_tuple)

    def _read(self, paths: list[str]) -> list[tuple[str, str]]:
        # Deferred so _fast's native/fallback choice is resolved lazily.
        from pydocs_mcp._fast import read_files_parallel
        return list(read_files_parallel(paths))

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "FileReadStage":
        return cls()

    def to_dict(self) -> dict:
        return {"type": "file_read"}


__all__ = ("FileReadStage",)
