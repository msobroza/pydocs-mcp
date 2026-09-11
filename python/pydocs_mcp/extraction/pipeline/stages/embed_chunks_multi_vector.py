"""EmbedChunksMultiVectorStage — multi-vector ingestion (late-interaction).

Sibling of :class:`EmbedChunksStage` for the late-interaction path. Splits
single-vector vs multi-vector ingestion by Protocol contract rather than
by a runtime branch inside one stage: this stage takes a
:class:`~pydocs_mcp.retrieval.protocols.MultiVectorEmbedder` (ColBERT-style,
one normalized vector per token) and splices the resulting
``MultiVector = list[np.ndarray]`` onto each :class:`Chunk.embedding`.

Both the :class:`~pydocs_mcp.extraction.embed_policy.EmbedPolicy` tier gate
and the ``existing_chunk_hashes`` budget are honored identically to
:class:`EmbedChunksStage`: only tier-eligible chunks are candidates, and for
each hash as many copies as are already persisted skip the embedder while the
excess is embedded (see ``_embed_budget``). The pipeline-hash + tier
invalidation in :class:`AssignChunkContentHashStage` keeps the cache honest
across embedder swaps and policy changes. Unlike the single-vector stage this
one records no ``embedded_with_model`` — see the comment in :meth:`run`.

The :meth:`from_dict` decoder requires
``BuildContext.multi_vector_embedder`` to be set; production wiring
constructs the embedder once at server / CLI startup (via the
``build_multi_vector_embedder(cfg)`` factory) and threads it into the
:class:`BuildContext`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from pydocs_mcp.extraction.embed_policy import EmbedPolicy
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.pipeline.stages._embed_budget import (
    indices_beyond_persisted_budget,
)
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import MultiVector
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
    # The SAME policy the dense stage and the chunk-hash stage read
    # (``embedding.dependency_policy`` / ``full_index_dependencies``). The
    # tier is already folded into every chunk hash under this preset; this
    # stage was cloned before the policy existed and simply never applied it,
    # so ``dependency_policy: none`` still vectorised every dependency chunk.
    embed_policy: EmbedPolicy = field(default_factory=EmbedPolicy)
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

        # Selective embed policy, identical to EmbedChunksStage: only
        # tier-eligible chunks get multi-vectors (project + promoted deps = all;
        # regular deps = doc pages only; ``none`` = nothing). Ineligible chunks
        # still persist and stay BM25-searchable, just without a multi-vector.
        tier = self.embed_policy.tier(state.files.target_kind, state.files.package_name)
        # Per-hash budget against the persisted counts, not membership — see
        # indices_beyond_persisted_budget. ``candidates`` are the eligible
        # positions still lacking a vector; ``excess`` indexes into that list.
        candidates = [
            i
            for i, c in enumerate(chunks)
            if c.embedding is None
            and self.embed_policy.should_embed(c.metadata.get("origin"), tier)
        ]
        excess = indices_beyond_persisted_budget(
            [chunks[i] for i in candidates], state.existing_chunk_hashes
        )
        to_embed_idx = [candidates[j] for j in excess]

        # Deliberately NO ``embedded_with_model`` here. ``packages.embedding_model``
        # is the DENSE embedder's identity: ``index_metadata`` records the dense
        # model, and multirepo's serve-time guard compares the column against
        # ``config.embedding.model_name`` for bundles with no metadata row.
        # Recording the late-interaction model there made every such LI bundle
        # fail that guard as a spurious mismatch. Left None, the guard treats
        # the bundle as uncheckable and permits it — exactly as before.
        if not to_embed_idx:
            return state

        embedded_by_hash: dict[str, MultiVector] = {}
        for start in range(0, len(to_embed_idx), self.batch_size):
            batch_idx = to_embed_idx[start : start + self.batch_size]
            texts = tuple(chunks[i].text for i in batch_idx)
            embs = await self.embedder.embed_chunks(texts)
            # strict=True surfaces buggy MultiVectorEmbedders that return
            # the wrong number of vectors instead of silently truncating.
            for i, emb in zip(batch_idx, embs, strict=True):
                embedded_by_hash[chunks[i].content_hash] = emb

        # Splice by HASH onto every still-vectorless ELIGIBLE copy (mirrors
        # the single-vector stage): identical hash ⇒ identical text ⇒
        # identical multi-vector, and the diff-merge — not this stage —
        # decides which copies become the new rows, so all of them must
        # carry it. Ineligible chunks never receive one, whatever their hash.
        eligible_positions = set(candidates)
        new_chunks = tuple(
            replace(c, embedding=embedded_by_hash[c.content_hash])
            if i in eligible_positions and c.content_hash in embedded_by_hash
            else c
            for i, c in enumerate(chunks)
        )
        return replace(state, chunks=replace(state.chunks, chunks=new_chunks))

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
        app_config = getattr(context, "app_config", None)
        return cls(
            embedder=embedder,
            batch_size=int(data.get("batch_size", _DEFAULT_BATCH_SIZE)),
            embed_policy=EmbedPolicy.from_config(getattr(app_config, "embedding", None)),
        )


__all__ = ("EmbedChunksMultiVectorStage",)
