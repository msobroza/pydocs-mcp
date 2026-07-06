"""EmbedChunksMultiVectorStage — multi-vector ingestion (late-interaction).

Sibling of :class:`EmbedChunksStage` for the late-interaction path. Splits
single-vector vs multi-vector ingestion by Protocol contract rather than
by a runtime branch inside one stage: this stage takes a
:class:`~pydocs_mcp.retrieval.protocols.MultiVectorEmbedder` (ColBERT-style,
one normalized vector per token) and splices the resulting
``MultiVector = list[np.ndarray]`` onto each :class:`Chunk.embedding`.

The ``existing_chunk_hashes`` skip set is honored identically to
:class:`EmbedChunksStage`: chunks whose ``content_hash`` is already in
the persisted map don't re-enter the embedder. The pipeline-hash
invalidation in :class:`AssignChunkContentHashStage` keeps the cache
honest across embedder swaps.

The :meth:`from_dict` decoder requires
``BuildContext.multi_vector_embedder`` to be set; production wiring
constructs the embedder once at server / CLI startup (via the
``build_multi_vector_embedder(cfg)`` factory) and threads it into the
:class:`BuildContext`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.retrieval.protocols import MultiVectorEmbedder

_DEFAULT_BATCH_SIZE = 32


@stage_registry.register("embed_chunks_multi_vector")
@dataclass(frozen=True, slots=True)
class EmbedChunksMultiVectorStage:
    """Compute a per-token multi-vector embedding for every chunk.

    Calls :meth:`MultiVectorEmbedder.embed_chunks` once per
    ``batch_size``-sized slice of ``state.chunks.chunks`` and returns a
    new :class:`IngestionState` whose chunks bundle carries the freshly
    computed multi-vectors. Chunk order is preserved.
    """

    embedder: MultiVectorEmbedder
    batch_size: int = _DEFAULT_BATCH_SIZE
    name: str = "embed_chunks_multi_vector"

    def __post_init__(self) -> None:
        # Guard against degenerate batch_size: 0 raises a cryptic
        # ``range() arg 3 must not be zero`` from stdlib, and negative
        # values silently produce an empty range → empty embeddings →
        # strict-zip mismatch. Fail loud at construction (mirrors
        # :class:`EmbedChunksStage`).
        if self.batch_size <= 0:
            raise ValueError(
                f"EmbedChunksMultiVectorStage.batch_size must be > 0, got {self.batch_size}",
            )

    async def run(self, state: IngestionState) -> IngestionState:
        chunks = state.chunks.chunks
        if not chunks:
            return state

        skip = state.existing_chunk_hashes or {}
        to_embed_idx = [
            i for i, c in enumerate(chunks) if c.embedding is None and c.content_hash not in skip
        ]

        # Always stamp the package with embedder identity, even if no
        # chunks need re-embedding — so a re-embed sweep can still see
        # the current model_name on fully-cached packages (parity with
        # the single-vector stage).
        new_package = state.package
        if state.package is not None:
            new_package = replace(
                state.package,
                embedding_model=self.embedder.model_name,
            )

        if not to_embed_idx:
            return replace(state, package=new_package)

        new_chunks = list(chunks)
        for start in range(0, len(to_embed_idx), self.batch_size):
            batch_idx = to_embed_idx[start : start + self.batch_size]
            texts = tuple(chunks[i].text for i in batch_idx)
            embs = await self.embedder.embed_chunks(texts)
            # strict=True surfaces buggy MultiVectorEmbedders that return
            # the wrong number of vectors instead of silently truncating.
            for i, emb in zip(batch_idx, embs, strict=True):
                new_chunks[i] = replace(chunks[i], embedding=emb)

        new_chunks_bundle = replace(state.chunks, chunks=tuple(new_chunks))
        return replace(state, chunks=new_chunks_bundle, package=new_package)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "embed_chunks_multi_vector"}
        if self.batch_size != _DEFAULT_BATCH_SIZE:
            out["batch_size"] = self.batch_size
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> EmbedChunksMultiVectorStage:
        embedder = getattr(context, "multi_vector_embedder", None)
        if embedder is None:
            raise ValueError(
                "EmbedChunksMultiVectorStage requires "
                "BuildContext.multi_vector_embedder to be set. Enable "
                "late-interaction embeddings by configuring "
                "``late_interaction.enabled: true`` in your AppConfig "
                "YAML so build_multi_vector_embedder(cfg) returns a real "
                "instance.",
            )
        return cls(
            embedder=embedder,
            batch_size=int(data.get("batch_size", _DEFAULT_BATCH_SIZE)),
        )


__all__ = ("EmbedChunksMultiVectorStage",)
