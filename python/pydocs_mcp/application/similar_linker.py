"""Query-driven cross-repo SIMILAR generation (spec §A1.2, AC27).

SIMILAR cross-edges are GENERATED at link time, not resolved from persisted
unresolved rows: vectors are chunk-level, they live only in the quantized
``.tq`` sidecar, and the sidecar has no read-back API — so the feasible
design re-embeds the SOURCE repo's project chunk texts with the serving
embedder and runs each query vector against the TARGET repo's ``.tq``
(the search API that exists). Strictly embedder-gated: the stamped identity
fingerprint of BOTH bundles and the serving embedder must be equal, else
the pair is skipped and counted in ``LinkReport.embedder_mismatches`` —
never a permissive "compatible-ish" comparison.

Not a ``uow_factory`` service: like ``WorkspaceLinker`` it spans TWO
bundles per call (read-only) plus a ``.tq`` sidecar outside any UnitOfWork.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.storage.cross_link_edge import CrossLinkEdge
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

if TYPE_CHECKING:
    from pydocs_mcp.application.workspace_linker import BundleHandle
    from pydocs_mcp.retrieval.protocols import Embedder

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SimilarPairOutcome:
    """What one ordered (source → target) SIMILAR pass produced."""

    edges: tuple[CrossLinkEdge, ...] = ()
    embedder_mismatch: bool = False
    seconds: float = 0.0
    # False only from the Null generator — tells the linker no SIMILAR
    # machinery is wired so it records no counters/timings at all.
    active: bool = True


@dataclass(frozen=True, slots=True)
class NullSimilarLinkGenerator:
    """``similar`` not in ``cross_repo.kinds`` (or no embedder): inert pairs."""

    async def generate_pair(self, source: BundleHandle, target: BundleHandle) -> SimilarPairOutcome:
        return SimilarPairOutcome(active=False)


@dataclass(frozen=True, slots=True)
class SimilarLinkGenerator:
    """One ordered bundle pair → SIMILAR cross-edges (spec §A1.2 mechanics)."""

    embedder: Embedder
    # (provider, model_name, dim) of the serving config — the third leg of
    # the strict gate alongside the two bundle fingerprints.
    serving_fingerprint: tuple[str, str, int]
    top_k: int
    min_score: float

    async def generate_pair(self, source: BundleHandle, target: BundleHandle) -> SimilarPairOutcome:
        started = time.monotonic()
        if not self._gate_open(source, target):
            return SimilarPairOutcome(embedder_mismatch=True, seconds=time.monotonic() - started)
        tq_path = Path(target.bundle_path).with_suffix(".tq")
        if not tq_path.exists():
            # AC31 posture: a missing sidecar warns and skips — never raises,
            # never counts as an embedder mismatch.
            logger.warning(
                "cross-repo similar: no .tq sidecar for %s (%s) — pair %s->%s skipped",
                target.project,
                tq_path,
                source.project,
                target.project,
            )
            return SimilarPairOutcome(seconds=time.monotonic() - started)
        queries = await _project_chunks(source)
        qname_of = {c_id: qname for qname, _text, c_id in await _project_chunks(target)}
        if not queries or not qname_of:
            return SimilarPairOutcome(seconds=time.monotonic() - started)
        embeddings = await self.embedder.embed_chunks([text for _q, text, _i in queries])
        # The strict gate above admits single-vector serving embedders only
        # (cross_repo rides ``config.embedding``); asarray is a no-op for the
        # ndarray shape they return.
        hits = await self._search(tq_path, [np.asarray(e, dtype=np.float32) for e in embeddings])
        edges = self._edges(source, target, queries, hits, qname_of)
        return SimilarPairOutcome(edges=edges, seconds=time.monotonic() - started)

    def _gate_open(self, source: BundleHandle, target: BundleHandle) -> bool:
        """STRICT identity: both bundle fingerprints AND the serving embedder."""
        if _fingerprint(source) != _fingerprint(target):
            return False
        return _fingerprint(source)[:3] == self.serving_fingerprint

    async def _search(
        self, tq_path: Path, vectors: Sequence[np.ndarray]
    ) -> tuple[np.ndarray, np.ndarray]:
        """Batch-search the target ``.tq``; returns ``(scores_2d, ids_2d)``."""
        queries = np.stack([np.asarray(v, dtype=np.float32) for v in vectors])
        dim = self.serving_fingerprint[2]
        async with TurboQuantUnitOfWork(index_path=tq_path, dim=dim) as uow:
            # ``IdMapIndex.search`` degrades gracefully: an empty index
            # returns ``(nq, 0)`` arrays and ``k > len(index)`` returns
            # fewer rows — no size guard needed.
            return await asyncio.to_thread(uow.index.search, queries, self.top_k)

    def _edges(
        self,
        source: BundleHandle,
        target: BundleHandle,
        queries: list[tuple[str, str, int]],
        hits: tuple[np.ndarray, np.ndarray],
        qname_of: dict[int, str],
    ) -> tuple[CrossLinkEdge, ...]:
        """Chunk hits → qname-keyed edges: dedup per qname pair (max score
        kept), threshold at ``min_score``, cap ``top_k`` per source qname."""
        scores_2d, ids_2d = hits
        best: dict[tuple[str, str], float] = {}
        for (src_qname, _text, _id), scores, ids in zip(queries, scores_2d, ids_2d, strict=True):
            for score, chunk_id in zip(scores.tolist(), ids.tolist(), strict=True):
                to_qname = qname_of.get(int(chunk_id))
                if not to_qname or float(score) < self.min_score:
                    continue
                key = (src_qname, to_qname)
                best[key] = max(best.get(key, float(score)), float(score))
        ranked: dict[str, list[tuple[float, str]]] = {}
        for (src_qname, to_qname), score in best.items():
            ranked.setdefault(src_qname, []).append((-score, to_qname))
        edges: list[CrossLinkEdge] = []
        for src_qname in sorted(ranked):
            for _neg, to_qname in sorted(ranked[src_qname])[: self.top_k]:
                edges.append(_similar_edge(source, target, src_qname, to_qname))
        return tuple(edges)


def _fingerprint(bundle: BundleHandle) -> tuple[str, str, int, str]:
    return (
        bundle.embedding_provider,
        bundle.embedding_model,
        bundle.embedding_dim,
        bundle.pipeline_hash,
    )


async def _project_chunks(bundle: BundleHandle) -> list[tuple[str, str, int]]:
    """``(qualified_name, text, chunk_id)`` for the bundle's project-source
    chunks. Rows without a qname (doc pages, composites) can't key a
    qname→qname edge and are skipped (spec §A1.2)."""
    async with bundle.uow_factory() as uow:
        chunks = await uow.chunks.list(filter={"package": PROJECT_PACKAGE_NAME})
    return [
        (str(c.metadata["qualified_name"]), c.text, c.id)
        for c in chunks
        if c.metadata.get("qualified_name") and c.id is not None
    ]


def _similar_edge(
    source: BundleHandle, target: BundleHandle, src_qname: str, to_qname: str
) -> CrossLinkEdge:
    # ``to_name`` repeats the target qname — the audit analogue for a
    # GENERATED edge (there is no original unresolved to_name to keep).
    return CrossLinkEdge(
        from_project=source.project,
        from_package=PROJECT_PACKAGE_NAME,
        from_node_id=src_qname,
        to_project=target.project,
        to_node_id=to_qname,
        to_name=to_qname,
        kind=ReferenceKind.SIMILAR,
    )
