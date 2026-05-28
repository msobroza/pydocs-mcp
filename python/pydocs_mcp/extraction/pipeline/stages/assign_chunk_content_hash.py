"""AssignChunkContentHashStage — rewrite chunk content_hash with pipeline-aware version.

Slotted after chunking + flatten (state.chunks.chunks populated with
pipeline-blind auto-hashes) and before LoadExistingChunkHashesStage
(which needs the pipeline-aware hash to match SQLite). Reads
pipeline_hash from BuildContext at from_dict time and uses it to rewrite
each chunk's content_hash.

Per spec Decision 4: pipeline_hash captures embedder identity + raw bytes
of ingestion.yaml. Any embedder swap or YAML edit invalidates every chunk's
hash, the diff-merge sees them all as 'added', and the existing add path
re-embeds them. No separate force-re-embed code path needed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import compute_chunk_content_hash


@stage_registry.register("assign_chunk_content_hash")
@dataclass(frozen=True, slots=True)
class AssignChunkContentHashStage:
    """Rewrite each chunk's content_hash with the pipeline-aware version."""

    pipeline_hash: str = ""
    name: str = "assign_chunk_content_hash"

    async def run(self, state: IngestionState) -> IngestionState:
        if not state.chunks.chunks or not self.pipeline_hash:
            return state
        new_chunks = tuple(
            replace(
                c,
                content_hash=compute_chunk_content_hash(
                    package=str(c.metadata.get("package", "")),
                    module=str(c.metadata.get("module", "")),
                    title=str(c.metadata.get("title", "")),
                    text=c.text,
                    pipeline_hash=self.pipeline_hash,
                ),
            )
            for c in state.chunks.chunks
        )
        new_chunks_bundle = replace(state.chunks, chunks=new_chunks)
        return replace(state, chunks=new_chunks_bundle)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> AssignChunkContentHashStage:
        return cls(pipeline_hash=getattr(context, "pipeline_hash", ""))

    def to_dict(self) -> dict[str, Any]:
        return {"type": "assign_chunk_content_hash"}


__all__ = ("AssignChunkContentHashStage",)
