"""EmbedChunksStage — batch-embed ``state.chunks.chunks`` during ingestion (AC-23).

Slots between :class:`FlattenStage` and :class:`ContentHashStage` in the
shipped ``pipelines/ingestion.yaml`` (wired by Task 24): once flatten has
materialized every per-tree :class:`~pydocs_mcp.models.Chunk`, this stage
computes a vector for each in fixed-size batches via the configured
:class:`~pydocs_mcp.retrieval.protocols.Embedder` and threads the result
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
from dataclasses import dataclass, field, replace
from typing import Any

from pydocs_mcp.extraction.embed_policy import EmbedPolicy
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import Embedding
from pydocs_mcp.retrieval.protocols import Embedder

_DEFAULT_BATCH_SIZE = 32


@stage_registry.register("embed_chunks")
@dataclass(frozen=True, slots=True)
class EmbedChunksStage:
    """Compute embeddings for every chunk in ``state.chunks.chunks``.

    Calls :meth:`Embedder.embed_chunks` once per ``batch_size``-sized slice
    of ``state.chunks.chunks`` and returns a new :class:`IngestionState`
    whose chunks bundle carries the freshly computed vectors. The order
    of chunks is preserved.
    """

    embedder: Embedder
    batch_size: int = _DEFAULT_BATCH_SIZE
    embed_policy: EmbedPolicy = field(default_factory=EmbedPolicy)
    name: str = "embed_chunks"

    def __post_init__(self) -> None:
        # Guard against degenerate batch_size: 0 raises a cryptic
        # ``range() arg 3 must not be zero`` from stdlib, and negative
        # values silently produce an empty range → empty embeddings →
        # strict-zip mismatch. Fail loud at construction instead.
        if self.batch_size <= 0:
            raise ValueError(
                f"EmbedChunksStage.batch_size must be > 0, got {self.batch_size}",
            )

    async def run(self, state: IngestionState) -> IngestionState:
        if not state.chunks.chunks:
            return state

        # Selective embed policy: only tier-eligible chunks get vectors
        # (project + promoted deps = all; regular deps = doc pages only;
        # dependency_policy "none" = nothing). Ineligible chunks still
        # persist to SQLite — they are FTS/BM25-searchable, just vectorless.
        tier = self.embed_policy.tier(state.files.target_kind, state.files.package_name)
        eligible = tuple(
            c
            for c in state.chunks.chunks
            if self.embed_policy.should_embed(c.metadata.get("origin"), tier)
        )

        skip = state.existing_chunk_hashes or {}
        chunks_to_embed = tuple(c for c in eligible if c.content_hash not in skip)

        # Stamp the package with embedder identity iff this package HAS
        # embeddings under the policy (any eligible chunk, cached or fresh) —
        # so ``IndexingService.invalidate_stale_embeddings`` re-embeds it on
        # a model change. Packages with no eligible chunks keep
        # embedding_model NULL and are intentionally never flagged stale.
        new_package = state.package
        if state.package is not None and eligible:
            new_package = replace(
                state.package,
                embedding_model=self.embedder.model_name,
            )

        if not chunks_to_embed:
            # Full skip: no embedder call at all. Chunks come out untouched
            # (their existing TQ vectors stay valid).
            return replace(state, package=new_package)

        # Embed only the chunks not in the skip set
        embeddings: list[Embedding] = []
        for i in range(0, len(chunks_to_embed), self.batch_size):
            batch = chunks_to_embed[i : i + self.batch_size]
            embs = await self.embedder.embed_chunks(
                tuple(c.text for c in batch),
            )
            embeddings.extend(embs)

        # strict=True surfaces buggy Embedders that return the wrong
        # number of vectors instead of silently truncating chunks_to_embed.
        embedded_by_hash = dict(
            zip(
                (c.content_hash for c in chunks_to_embed),
                embeddings,
                strict=True,
            )
        )

        # Splice embeddings back into chunks at the right positions;
        # skipped chunks (not in embedded_by_hash) come out with their
        # existing embedding (typically None — their vector lives in TQ).
        new_chunks = tuple(
            replace(c, embedding=embedded_by_hash[c.content_hash])
            if c.content_hash in embedded_by_hash
            else c
            for c in state.chunks.chunks
        )
        new_chunks_bundle = replace(state.chunks, chunks=new_chunks)
        return replace(state, chunks=new_chunks_bundle, package=new_package)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> EmbedChunksStage:
        if getattr(context, "embedder", None) is None:
            raise ValueError(
                "EmbedChunksStage requires BuildContext.embedder to be set. "
                "Enable embeddings by configuring 'embedding.model_name' "
                "in YAML so build_embedder(cfg) returns a real instance.",
            )
        app_config = getattr(context, "app_config", None)
        return cls(
            embedder=context.embedder,
            batch_size=data.get("batch_size", _DEFAULT_BATCH_SIZE),
            embed_policy=EmbedPolicy.from_config(getattr(app_config, "embedding", None)),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "embed_chunks"}
        if self.batch_size != _DEFAULT_BATCH_SIZE:
            d["batch_size"] = self.batch_size
        return d


__all__ = ("EmbedChunksStage",)
