"""GraphExpandStep — dense-seeded reference-graph expansion (embedding-centric).

Activates the reference graph (``node_references``: CALLS / IMPORTS /
INHERITS / MENTIONS) as a *retrieval* signal. Today the graph is only read
single-hop by the ``lookup`` MCP tool; it is never used to rank ``search``
results. This step closes that gap **without** RRF or BM25 — the seeds come
from the dense (embedding) candidate list, so structural expansion starts
from semantically-grounded anchors rather than lexical name matches (the
weakness of substring-seeded graph-RAG systems).

Flow (runs AFTER the dense fetch+score, BEFORE top-k/limit):

1. Take the top-``top_s`` dense candidates as seeds, each with its cosine
   relevance. Seeds are addressed by ``chunk.metadata['qualified_name']`` —
   the only chunk↔graph join key (chunk metadata carries no ``node_id``, and
   for code nodes ``node_id == qualified_name`` anyway).
2. Expand ``max_depth`` hops over the graph via the existing
   ``uow.references`` surface (``find_callers`` for who-calls-the-seed,
   ``find_callees`` for what-the-seed-delegates-to), keeping only the
   configured edge ``kinds``. Bounded depth + a ``visited`` set guard the
   cycles that CALLS/IMPORTS edges form. Each discovered neighbour scores
   ``seed_relevance * decay**hop``.
3. Re-hydrate discovered qnames to chunks and **merge** them into the dense
   list keyed on ``qualified_name``, keeping ``max(dense_sim, graph_score)``.
   No reciprocal-rank fusion, no second branch — a single embedding-centric
   ranking.

Safety contract: if the candidate list is empty/non-Chunk, or the graph
yields nothing new, the input state is returned **unchanged** — so adding
this step to a pipeline can only help (it degrades to dense-only).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from pydocs_mcp.filters import FieldIn
from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.storage.protocols import UnitOfWork

# WHY: single source of truth for every default — referenced from field
# defaults, to_dict omit-when-default, and from_dict YAML-fallback.
_DEFAULT_TOP_S = 10
_DEFAULT_MAX_DEPTH = 1
_DEFAULT_DECAY = 0.5
_DEFAULT_DIRECTIONS: tuple[str, ...] = ("callers", "callees")
_DEFAULT_KINDS: tuple[str, ...] = ("calls", "inherits")
_DEFAULT_NEIGHBORS_PER_SEED = 25
_DEFAULT_NAME = "graph_expand"

# Depth is clamped to this — two indexed single-hop rounds is the practical
# ceiling for interactive latency, and deeper walks drift off-topic. Bumping
# this would also warrant a dedicated from_node_id index (find_callees keys on
# the non-leading column of the composite ix_refs_from).
_MAX_DEPTH_CAP = 2
_VALID_DIRECTIONS = frozenset({"callers", "callees"})

_QNAME_KEY = "qualified_name"


def _qname(chunk: Chunk) -> str | None:
    """The chunk's graph join key, or None if it carries no qualified_name."""
    value = chunk.metadata.get(_QNAME_KEY)
    return value if value else None


@step_registry.register("graph_expand")
@dataclass(frozen=True, slots=True)
class GraphExpandStep(RetrieverStep):
    """Dense-seeded graph expansion, fused into the dense list (no RRF/BM25).

    Strict-gate ``from_dict`` mirrors
    :class:`~pydocs_mcp.retrieval.steps.llm_tree_reasoning.LlmTreeReasoningStep`:
    ``context.uow_factory`` must be non-None at YAML-build time (a missing
    factory is a composition-root wiring bug, not user input), so the
    pipeline fails at startup rather than on the first query.
    """

    uow_factory: Callable[[], UnitOfWork] = field(kw_only=True)
    top_s: int = field(default=_DEFAULT_TOP_S, kw_only=True)
    max_depth: int = field(default=_DEFAULT_MAX_DEPTH, kw_only=True)
    decay: float = field(default=_DEFAULT_DECAY, kw_only=True)
    directions: tuple[str, ...] = field(default=_DEFAULT_DIRECTIONS, kw_only=True)
    kinds: tuple[str, ...] = field(default=_DEFAULT_KINDS, kw_only=True)
    neighbors_per_seed: int = field(default=_DEFAULT_NEIGHBORS_PER_SEED, kw_only=True)
    name: str = field(default=_DEFAULT_NAME, kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        candidates = state.candidates
        # Safety: only operate on a non-empty Chunk list. Member lists and
        # empty/None candidates pass through untouched (dense-only behaviour).
        if not isinstance(candidates, ChunkList) or not candidates.items:
            return state
        seeds = self._select_seeds(candidates.items)
        if not seeds:
            return state

        async with self.uow_factory() as uow:
            best_score = await self._expand(uow, seeds)
            seed_qnames = {qname for qname, _ in seeds}
            discovered = {
                qname: score for qname, score in best_score.items() if qname not in seed_qnames
            }
            if not discovered:
                return state
            neighbour_chunks = await self._hydrate(uow, discovered)

        if not neighbour_chunks:
            return state
        merged = _merge(candidates.items, neighbour_chunks)
        return replace(state, candidates=ChunkList(items=merged))

    def _select_seeds(self, items: tuple[Chunk, ...]) -> list[tuple[str, float]]:
        """Top-``top_s`` dense candidates as (qualified_name, dense_sim) seeds.

        Highest dense similarity wins per qname; candidates without a
        qualified_name can't be graph-addressed and are skipped.
        """
        ranked = sorted(items, key=lambda c: c.relevance or 0.0, reverse=True)
        seeds: dict[str, float] = {}
        for chunk in ranked[: self.top_s]:
            qname = _qname(chunk)
            if qname is None:
                continue
            sim = chunk.relevance or 0.0
            if sim > seeds.get(qname, float("-inf")):
                seeds[qname] = sim
        return list(seeds.items())

    async def _expand(
        self,
        uow: UnitOfWork,
        seeds: list[tuple[str, float]],
    ) -> dict[str, float]:
        """Bounded BFS over the reference graph; returns qname -> best score.

        Carries each frontier node's *originating seed similarity* forward so
        a neighbour ``hop`` edges out scores ``seed_sim * decay**hop``. A
        ``visited`` set bounds the cycles CALLS/IMPORTS edges form; ``max``
        keeps the strongest score when a node is reachable from several seeds.
        """
        best_score: dict[str, float] = {}
        visited: set[str] = {qname for qname, _ in seeds}
        frontier = list(seeds)
        for hop in range(1, max(1, self.max_depth) + 1):
            score_factor = self.decay**hop
            next_frontier: list[tuple[str, float]] = []
            for qname, base_sim in frontier:
                score = base_sim * score_factor
                for neighbour in await self._neighbours(uow, qname):
                    if score > best_score.get(neighbour, float("-inf")):
                        best_score[neighbour] = score
                    if neighbour not in visited:
                        visited.add(neighbour)
                        next_frontier.append((neighbour, base_sim))
            if not next_frontier:
                break
            frontier = next_frontier
        return best_score

    async def _neighbours(self, uow: UnitOfWork, qname: str) -> list[str]:
        """Neighbour qnames of ``qname`` across the configured directions/kinds.

        - ``callers`` → ``find_callers(target_node_id=qname)``, neighbour is
          the edge's ``from_node_id`` (who references the seed).
        - ``callees`` → ``find_callees(from_node_id=qname)``, neighbour is the
          edge's ``to_node_id`` (what the seed references); unresolved edges
          (``to_node_id`` None/"") are skipped — they point outside the index.
        """
        found: list[str] = []
        if "callers" in self.directions:
            for ref in await uow.references.find_callers(target_node_id=qname):
                if str(ref.kind) in self.kinds and ref.from_node_id:
                    found.append(ref.from_node_id)
        if "callees" in self.directions:
            for ref in await uow.references.find_callees(from_node_id=qname):
                if str(ref.kind) in self.kinds and ref.to_node_id:
                    found.append(ref.to_node_id)
        return found[: self.neighbors_per_seed]

    async def _hydrate(
        self,
        uow: UnitOfWork,
        discovered: dict[str, float],
    ) -> list[Chunk]:
        """Fetch chunks for discovered qnames, stamped with the graph score.

        Passes ``FieldIn`` so a real SQLite backend filters via ``IN (...)``,
        then narrows client-side — backend-neutral, since some stores (the
        test fakes) ignore Filter-tree predicates and return everything.
        Keeps the longest-text chunk per qname (one representative).
        """
        rows = await uow.chunks.list(
            filter=FieldIn(field=_QNAME_KEY, values=tuple(discovered)),
        )
        best_chunk: dict[str, Chunk] = {}
        for row in rows:
            qname = _qname(row)
            if qname is None or qname not in discovered:
                continue
            incumbent = best_chunk.get(qname)
            if incumbent is None or len(row.text) > len(incumbent.text):
                best_chunk[qname] = row
        return [replace(c, relevance=discovered[q]) for q, c in best_chunk.items()]

    def to_dict(self) -> dict:
        d: dict = {"type": "graph_expand"}
        if self.top_s != _DEFAULT_TOP_S:
            d["top_s"] = self.top_s
        if self.max_depth != _DEFAULT_MAX_DEPTH:
            d["max_depth"] = self.max_depth
        if self.decay != _DEFAULT_DECAY:
            d["decay"] = self.decay
        if self.directions != _DEFAULT_DIRECTIONS:
            d["directions"] = list(self.directions)
        if self.kinds != _DEFAULT_KINDS:
            d["kinds"] = list(self.kinds)
        if self.neighbors_per_seed != _DEFAULT_NEIGHBORS_PER_SEED:
            d["neighbors_per_seed"] = self.neighbors_per_seed
        if self.name != _DEFAULT_NAME:
            d["name"] = self.name
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> GraphExpandStep:
        if context.uow_factory is None:
            raise ValueError(
                "GraphExpandStep requires BuildContext.uow_factory. "
                "Production wiring in __main__.py / server.py sets this; "
                "tests must pass it explicitly.",
            )
        max_depth = data.get("max_depth", _DEFAULT_MAX_DEPTH)
        # Clamp rather than reject — out-of-range depth is a tuning typo, not a
        # wiring bug; silently bounding keeps the safety contract intact.
        max_depth = max(1, min(_MAX_DEPTH_CAP, max_depth))
        directions = tuple(data.get("directions", _DEFAULT_DIRECTIONS))
        invalid = [d for d in directions if d not in _VALID_DIRECTIONS]
        if invalid:
            raise ValueError(
                f"GraphExpandStep.directions must be a subset of "
                f"{sorted(_VALID_DIRECTIONS)}; got unexpected {invalid}.",
            )
        return cls(
            uow_factory=context.uow_factory,
            top_s=data.get("top_s", _DEFAULT_TOP_S),
            max_depth=max_depth,
            decay=data.get("decay", _DEFAULT_DECAY),
            directions=directions,
            kinds=tuple(data.get("kinds", _DEFAULT_KINDS)),
            neighbors_per_seed=data.get("neighbors_per_seed", _DEFAULT_NEIGHBORS_PER_SEED),
            name=data.get("name", _DEFAULT_NAME),
        )


def _merge(dense_items: tuple[Chunk, ...], neighbour_chunks: list[Chunk]) -> tuple[Chunk, ...]:
    """Merge graph neighbours into the dense list — embedding-centric, no RRF.

    Keyed on ``qualified_name`` (chunks without one fall back to a synthetic
    ``id:<id>`` / object-identity key so they never collide or drop). For a
    chunk present in both, keep the stronger score (``max(dense_sim,
    graph_score)``) on the dense representative; pure graph-discovered chunks
    are appended. Sorted by relevance descending. Because ``decay < 1`` a
    graph-only neighbour enters below its seed but can outrank weak dense-tail
    hits — the intended structural lift, no rank fusion.
    """

    def key(chunk: Chunk) -> str:
        qname = _qname(chunk)
        if qname is not None:
            return qname
        return f"id:{chunk.id}" if chunk.id is not None else f"obj:{id(chunk)}"

    merged: dict[str, Chunk] = {key(c): c for c in dense_items}
    for chunk in neighbour_chunks:
        k = key(chunk)
        incumbent = merged.get(k)
        if incumbent is None:
            merged[k] = chunk
        elif (chunk.relevance or 0.0) > (incumbent.relevance or 0.0):
            merged[k] = replace(incumbent, relevance=chunk.relevance)
    return tuple(sorted(merged.values(), key=lambda c: c.relevance or 0.0, reverse=True))


__all__ = ("GraphExpandStep",)
