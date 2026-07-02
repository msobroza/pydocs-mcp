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
from dataclasses import dataclass, field, replace
from typing import Any

from pydocs_mcp.extraction.embed_policy import EmbedPolicy
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import compute_chunk_content_hash


@stage_registry.register("assign_chunk_content_hash")
@dataclass(frozen=True, slots=True)
class AssignChunkContentHashStage:
    """Rewrite each chunk's content_hash with the pipeline-aware version.

    The package's embed TIER (``EmbedPolicy.tier`` — full / doc_pages / none)
    is folded into the hash slot alongside ``pipeline_hash``. Promoting a
    dependency (``--full-dep`` / ``embedding.full_index_dependencies``) or
    changing ``embedding.dependency_policy`` therefore invalidates exactly the
    affected packages' chunk hashes: the diff-merge re-inserts them, the embed
    stage re-embeds them under the new tier, and demoted packages' old rows
    (+ vectors) are dropped — no global re-embed, no separate force path.
    """

    pipeline_hash: str = ""
    embed_policy: EmbedPolicy = field(default_factory=EmbedPolicy)
    name: str = "assign_chunk_content_hash"

    async def run(self, state: IngestionState) -> IngestionState:
        if not state.chunks.chunks or not self.pipeline_hash:
            return state
        tier = self.embed_policy.tier(state.files.target_kind, state.files.package_name)
        effective_hash = f"{self.pipeline_hash}|tier:{tier}"
        new_chunks = tuple(
            replace(
                c,
                content_hash=compute_chunk_content_hash(
                    package=str(c.metadata.get("package", "")),
                    module=str(c.metadata.get("module", "")),
                    title=str(c.metadata.get("title", "")),
                    text=c.text,
                    pipeline_hash=effective_hash,
                ),
            )
            for c in state.chunks.chunks
        )
        new_chunks_bundle = replace(state.chunks, chunks=new_chunks)
        return replace(state, chunks=new_chunks_bundle)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> AssignChunkContentHashStage:
        app_config = getattr(context, "app_config", None)
        return cls(
            pipeline_hash=getattr(context, "pipeline_hash", ""),
            embed_policy=EmbedPolicy.from_config(getattr(app_config, "embedding", None)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"type": "assign_chunk_content_hash"}


__all__ = ("AssignChunkContentHashStage",)
