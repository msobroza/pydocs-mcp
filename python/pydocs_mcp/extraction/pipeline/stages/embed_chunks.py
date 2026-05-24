"""EmbedChunksStage — batch-embed ``state.chunks`` during ingestion (AC-23).

Slots between :class:`FlattenStage` and :class:`ContentHashStage` in the
shipped ``pipelines/ingestion.yaml`` (wired by Task 24): once flatten has
materialized every per-tree :class:`~pydocs_mcp.models.Chunk`, this stage
computes a vector for each in fixed-size batches via the configured
:class:`~pydocs_mcp.storage.protocols.Embedder` and threads the result
back onto the chunk via ``dataclasses.replace(chunk, embedding=...)``.

Idempotent — re-running the stage on a state whose chunks already carry
embeddings recomputes them. The cheap path is the existing per-package
content-hash skip in :class:`ProjectIndexer`: unchanged packages never
re-enter the pipeline so this stage doesn't run at all.

The :meth:`from_dict` decoder requires ``BuildContext.embedder`` to be
set; production wiring constructs an :class:`Embedder` once at
server / CLI startup (via ``build_embedder(cfg)``) and threads it into
the :class:`BuildContext`. Tests pass a :class:`MockEmbedder`.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import Embedding
from pydocs_mcp.storage.protocols import Embedder

_DEFAULT_BATCH_SIZE = 32


@stage_registry.register("embed_chunks")
@dataclass(frozen=True, slots=True)
class EmbedChunksStage:
    """Compute embeddings for every chunk in ``state.chunks``.

    Calls :meth:`Embedder.embed_chunks` once per ``batch_size``-sized slice
    of ``state.chunks`` and returns a new :class:`IngestionState` whose
    ``chunks`` tuple carries the freshly computed vectors. The order of
    chunks is preserved.
    """

    embedder: Embedder
    batch_size: int = _DEFAULT_BATCH_SIZE
    name: str = "embed_chunks"

    async def run(self, state: IngestionState) -> IngestionState:
        if not state.chunks:
            return state
        embeddings: list[Embedding] = []
        for i in range(0, len(state.chunks), self.batch_size):
            batch = state.chunks[i:i + self.batch_size]
            embs = await self.embedder.embed_chunks(
                tuple(c.text for c in batch),
            )
            embeddings.extend(embs)
        new_chunks = tuple(
            replace(c, embedding=emb)
            for c, emb in zip(state.chunks, embeddings)
        )
        return replace(state, chunks=new_chunks)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> "EmbedChunksStage":
        if getattr(context, "embedder", None) is None:
            raise ValueError(
                "EmbedChunksStage requires BuildContext.embedder to be set. "
                "Enable embeddings by configuring 'embedding.model_name' "
                "in YAML so build_embedder(cfg) returns a real instance.",
            )
        return cls(
            embedder=context.embedder,
            batch_size=data.get("batch_size", _DEFAULT_BATCH_SIZE),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "embed_chunks"}
        if self.batch_size != _DEFAULT_BATCH_SIZE:
            d["batch_size"] = self.batch_size
        return d


__all__ = ("EmbedChunksStage",)
