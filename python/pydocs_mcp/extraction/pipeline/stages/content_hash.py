"""ContentHashStage — fills ``state.content_hash``, the package-level hash.

The package hash drives whole-package cache invalidation. Per-node
``DocumentNode.content_hash`` values are computed inside each chunker
and ride on the trees instead — they don't flow through state.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry


@stage_registry.register("content_hash")
@dataclass(frozen=True, slots=True)
class ContentHashStage:
    name: str = "content_hash"

    async def run(self, state: IngestionState) -> IngestionState:
        # I7 commit 2 — read paths from the FileBundle, fall back to the
        # legacy flat field for tests that haven't migrated. Write to both
        # the bundle AND the flat field; commit 3 drops the flat duplicate.
        paths = state.files.paths if state.files.paths else state.paths
        h = await asyncio.to_thread(self._hash, list(paths))
        new_files = replace(state.files, content_hash=h)
        return replace(state, files=new_files, content_hash=h)

    def _hash(self, paths: list[str]) -> str:
        # Deferred so _fast's native/fallback choice is resolved lazily.
        from pydocs_mcp._fast import hash_files
        result = hash_files(paths)
        # hash_files may return str (fallback) or bytes (some native builds).
        # Normalize so downstream consumers see a stable str regardless.
        return result if isinstance(result, str) else result.hex()

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "ContentHashStage":
        return cls()

    def to_dict(self) -> dict:
        return {"type": "content_hash"}


__all__ = ("ContentHashStage",)
