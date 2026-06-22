"""SynthesizeSimilarEdgesStage — embedding-kNN ``similar`` reference edges.

Runs AFTER :class:`EmbedChunksStage` (so chunks carry embeddings). For each
freshly-embedded chunk that has a ``qualified_name``, it finds the top-``m``
nearest other chunks by embedding cosine and appends
``NodeReference(kind=SIMILAR)`` rows to ``state.refs.references`` — densifying
the AST-only reference graph with semantic links, so ``graph_expand`` (with
``similar`` in its ``kinds``) can reach related code that has no call/inherit
edge.

Opt-in: a no-op unless ``reference_graph.similar_edges.enabled`` (installed into
the module-level singleton by ``configure_from_app_config``). Skips chunks with
no embedding (cached on an incremental reindex) and multi-vector embeddings
(late-interaction). The kNN matmul runs off the event loop via
``asyncio.to_thread``.

LIMITATION (incremental reindex): the kNN is computed only over chunks embedded
in THIS run. On a partial reindex of a changed package, unchanged chunks come
out of EmbedChunksStage with ``embedding=None`` (their vector lives only in the
TurboQuant ``.tq`` sidecar, which has no read-back API), so they are excluded —
while the package's prior ``similar`` edges were already swept by the reference
delete. Net: similar edges are complete only after a full / ``--force`` index;
an incremental reindex of a touched package yields edges among its re-embedded
chunks only. The feature is opt-in/experimental; regenerate with ``index
--force`` for a complete similar-edge graph.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import Chunk, is_multi_vector
from pydocs_mcp.retrieval.config import SimilarEdgesConfig
from pydocs_mcp.storage.node_reference import NodeReference

# Module-level singleton — installed by configure_from_app_config at startup.
# Default disabled so unit tests / non-YAML callers get the safe no-op baseline.
_SIMILAR_CONFIG: SimilarEdgesConfig = SimilarEdgesConfig()


def _get_similar_config() -> SimilarEdgesConfig:
    return _SIMILAR_CONFIG


def _set_similar_config(cfg: SimilarEdgesConfig) -> None:
    global _SIMILAR_CONFIG
    _SIMILAR_CONFIG = cfg


def _eligible(chunk: Chunk) -> str | None:
    """Return the chunk's qualified_name if it can seed a similar edge, else None."""
    emb = chunk.embedding
    if emb is None or is_multi_vector(emb):
        return None
    qname = chunk.metadata.get("qualified_name")
    return qname or None


def _knn_edges(chunks: tuple[Chunk, ...], package: str, top_m: int) -> list[NodeReference]:
    """Top-``m`` cosine-neighbour ``similar`` edges among ``chunks``.

    One row per (source, neighbour); kNN is directed (A's top-m need not list
    B), which is fine — graph_expand traverses both directions, so a single
    A->B row is reachable from both nodes.
    """
    pairs = [(qn, c.embedding) for c in chunks if (qn := _eligible(c))]
    # Dedup qnames (a symbol can have multiple chunks) — keep the first vector.
    seen: dict[str, np.ndarray] = {}
    for qn, emb in pairs:
        seen.setdefault(qn, np.asarray(emb, dtype=np.float32))
    if len(seen) < 2:
        return []
    qnames = list(seen)
    matrix = np.vstack([seen[q] for q in qnames])
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    unit = matrix / norms
    sims = unit @ unit.T
    np.fill_diagonal(sims, -np.inf)  # never link a node to itself

    k = min(top_m, len(qnames) - 1)
    edges: list[NodeReference] = []
    for i, src in enumerate(qnames):
        # top-k neighbour indices for row i, by descending similarity.
        top = np.argpartition(sims[i], -k)[-k:]
        for j in top[np.argsort(sims[i][top])[::-1]]:
            edges.append(
                NodeReference(
                    from_package=package,
                    from_node_id=src,
                    to_name=qnames[j],
                    to_node_id=qnames[j],
                    kind=ReferenceKind.SIMILAR,
                )
            )
    return edges


@stage_registry.register("synthesize_similar_edges")
@dataclass(frozen=True, slots=True)
class SynthesizeSimilarEdgesStage:
    """Append embedding-kNN ``similar`` edges to ``state.refs.references``."""

    name: str = "synthesize_similar_edges"

    async def run(self, state: IngestionState) -> IngestionState:
        cfg = _get_similar_config()
        if not cfg.enabled or not state.chunks.chunks:
            return state
        edges = await asyncio.to_thread(
            _knn_edges,
            state.chunks.chunks,
            state.files.package_name,
            cfg.top_m,
        )
        if not edges:
            return state
        new_refs = replace(
            state.refs,
            references=tuple(state.refs.references) + tuple(edges),
        )
        return replace(state, refs=new_refs)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> SynthesizeSimilarEdgesStage:
        # Runtime knobs (enabled / top_m) come from the singleton config set by
        # configure_from_app_config, so the YAML node is minimal.
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return {"type": "synthesize_similar_edges"}


__all__ = (
    "SynthesizeSimilarEdgesStage",
    "_get_similar_config",
    "_set_similar_config",
)
