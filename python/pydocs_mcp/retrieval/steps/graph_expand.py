"""GraphExpandStep — dense-seeded reference-graph expansion (embedding-centric).

Activates the reference graph (``node_references``: CALLS / IMPORTS /
INHERITS / MENTIONS) as a *retrieval* signal. Today the graph is only read
single-hop by ``get_references`` / ``get_symbol``; it is never used to rank
``search_codebase`` results. This step closes that gap **without** RRF or BM25 — the seeds come
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
   ``seed_relevance * decay**hop``, further scaled by the product of the
   traversed edges' ``kind_weights`` (per-kind trust: a MENTIONS edge is
   weaker evidence than a CALLS edge; unlisted kinds weigh 1.0).
3. Re-hydrate discovered qnames to chunks and **merge** them into the dense
   list keyed on ``qualified_name``, keeping ``max(dense_sim, graph_score)``.
   No reciprocal-rank fusion, no second branch — a single embedding-centric
   ranking.

Safety contract: if the candidate list is empty/non-Chunk, or the graph
yields nothing new, the input state is returned **unchanged** — so adding
this step to a pipeline can only help (it degrades to dense-only).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

from pydocs_mcp.filters import FieldIn
from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    step_registry,
    step_to_yaml_dict,
    yaml_kwargs,
)
from pydocs_mcp.storage.protocols import UnitOfWork

# WHY: single source of truth for every default — referenced from field
# defaults, to_dict omit-when-default, and from_dict YAML-fallback.
_DEFAULT_TOP_S = 10
_DEFAULT_MAX_DEPTH = 1
# WHY 0.9 (not a steeper decay): a discovered neighbour scores seed_sim*decay^hop
# and competes on the dense cosine scale, which is COMPRESSED (top hits sit in a
# narrow band). A steep decay (e.g. 0.5) drops a rank-1 seed's neighbour below
# the dense tail so it never enters the top-k — measured near-inert on the
# repoqa-structural benchmark (recall@10 30%->40% at decay 0.5 vs 30%->100% at
# 0.9). 0.9 ranks the neighbour just below its seed; safe for precision because
# the real answer (the seed) always outranks its own decayed neighbours.
_DEFAULT_DECAY = 0.9
_DEFAULT_DIRECTIONS: tuple[str, ...] = ("callers", "callees")
_DEFAULT_KINDS: tuple[str, ...] = ("calls", "inherits")
_DEFAULT_NEIGHBORS_PER_SEED = 25
# Empty means every traversed kind weighs 1.0 — byte-identical to the
# pre-kind_weights behaviour, so the benchmarked default ranking is untouched.
_DEFAULT_KIND_WEIGHTS: tuple[tuple[str, float], ...] = ()
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


def _normalize_kind_weights(value: Any) -> tuple[tuple[str, float], ...]:
    """Canonicalize a YAML ``kind_weights`` mapping (or pair sequence).

    Weights must be finite numbers > 0 — a zero/negative weight is edge
    *removal*, which is what ``kinds`` is for. Entries of exactly 1.0 are
    no-ops and dropped, so omit-when-default ``to_dict`` round-trips cleanly.
    Output is sorted by kind for a deterministic YAML byte encoding.
    """
    pairs = value.items() if isinstance(value, Mapping) else value
    weights: dict[str, float] = {}
    for kind, weight in pairs:
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight <= 0
        ):
            raise ValueError(
                f"GraphExpandStep.kind_weights[{kind!r}] must be a finite "
                f"number > 0; got {weight!r}."
            )
        if weight != 1.0:
            weights[str(kind)] = float(weight)
    return tuple(sorted(weights.items()))


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
    # Canonical (kind, weight) pairs — YAML-facing shape is a mapping
    # (``kind_weights: {mentions: 0.5}``); pairs keep the frozen dataclass
    # hashable. A kind absent here weighs 1.0.
    kind_weights: tuple[tuple[str, float], ...] = field(default=_DEFAULT_KIND_WEIGHTS, kw_only=True)
    name: str = field(default=_DEFAULT_NAME, kw_only=True)
    _YAML_KEYS: ClassVar[tuple[str, ...]] = (
        "top_s",
        "max_depth",
        "decay",
        "directions",
        "kinds",
        "neighbors_per_seed",
        "kind_weights",
        "name",
    )

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

        Carries each frontier node's *originating seed similarity* and the
        product of per-edge ``kind_weights`` along its path forward, so a
        neighbour ``hop`` edges out scores ``seed_sim * decay**hop *
        path_weight``. A ``visited`` set bounds the cycles CALLS/IMPORTS
        edges form; ``max`` keeps the strongest score when a node is
        reachable from several seeds.
        """
        weight_of = dict(self.kind_weights)
        best_score: dict[str, float] = {}
        visited: set[str] = {qname for qname, _ in seeds}
        frontier = [(qname, sim, 1.0) for qname, sim in seeds]
        for hop in range(1, max(1, self.max_depth) + 1):
            frontier = await self._expand_one_hop(
                uow, frontier, self.decay**hop, weight_of, best_score, visited
            )
            if not frontier:
                break
        return best_score

    async def _expand_one_hop(
        self,
        uow: UnitOfWork,
        frontier: list[tuple[str, float, float]],
        score_factor: float,
        weight_of: dict[str, float],
        best_score: dict[str, float],
        visited: set[str],
    ) -> list[tuple[str, float, float]]:
        """One BFS hop: score each frontier node's neighbours, return the next
        frontier. Mutates ``best_score`` (max per qname) and ``visited`` (cycle
        guard) in place; ``base_sim`` (the originating seed similarity) is
        carried forward so the next hop decays from the seed, not the parent,
        and ``path_weight`` compounds the traversed edges' kind weights. The
        first-discovered path claims a node's frontier slot (same rule as the
        cycle guard); ``best_score`` still takes the max across all paths."""
        next_frontier: list[tuple[str, float, float]] = []
        for qname, base_sim, path_weight in frontier:
            for neighbour, kind_weight in await self._neighbours(uow, qname, weight_of):
                weight = path_weight * kind_weight
                score = base_sim * score_factor * weight
                if score > best_score.get(neighbour, float("-inf")):
                    best_score[neighbour] = score
                if neighbour not in visited:
                    visited.add(neighbour)
                    next_frontier.append((neighbour, base_sim, weight))
        return next_frontier

    async def _neighbours(
        self, uow: UnitOfWork, qname: str, weight_of: dict[str, float]
    ) -> list[tuple[str, float]]:
        """(neighbour qname, kind weight) pairs across the configured
        directions/kinds; a kind absent from ``weight_of`` weighs 1.0.

        - ``callers`` → ``find_callers(target_node_id=qname)``, neighbour is
          the edge's ``from_node_id`` (who references the seed).
        - ``callees`` → ``find_callees(from_node_id=qname)``, neighbour is the
          edge's ``to_node_id`` (what the seed references); unresolved edges
          (``to_node_id`` None/"") are skipped — they point outside the index.
        """
        found: list[tuple[str, float]] = []
        if "callers" in self.directions:
            for ref in await uow.references.find_callers(target_node_id=qname):
                if str(ref.kind) in self.kinds and ref.from_node_id:
                    found.append((ref.from_node_id, weight_of.get(str(ref.kind), 1.0)))
        if "callees" in self.directions:
            for ref in await uow.references.find_callees(from_node_id=qname):
                if str(ref.kind) in self.kinds and ref.to_node_id:
                    found.append((ref.to_node_id, weight_of.get(str(ref.kind), 1.0)))
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
        out = step_to_yaml_dict(self, type_name="graph_expand", keys=self._YAML_KEYS)
        # YAML-facing shape is a mapping, not the internal pair-tuple.
        if "kind_weights" in out:
            out["kind_weights"] = dict(self.kind_weights)
        return out

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> GraphExpandStep:
        if context.uow_factory is None:
            raise ValueError(
                "GraphExpandStep requires BuildContext.uow_factory. "
                "Production wiring in __main__.py / server.py sets this; "
                "tests must pass it explicitly.",
            )
        kwargs = yaml_kwargs(data, cls, cls._YAML_KEYS)
        # Re-read kind_weights from the raw data: yaml_kwargs' tuple coercion
        # would keep only a YAML mapping's KEYS and drop the weights.
        kwargs["kind_weights"] = _normalize_kind_weights(
            data.get("kind_weights", _DEFAULT_KIND_WEIGHTS)
        )
        # Clamp rather than reject — out-of-range depth is a tuning typo, not a
        # wiring bug; silently bounding keeps the safety contract intact.
        kwargs["max_depth"] = max(1, min(_MAX_DEPTH_CAP, kwargs["max_depth"]))
        invalid = [d for d in kwargs["directions"] if d not in _VALID_DIRECTIONS]
        if invalid:
            raise ValueError(
                f"GraphExpandStep.directions must be a subset of "
                f"{sorted(_VALID_DIRECTIONS)}; got unexpected {invalid}.",
            )
        return cls(uow_factory=context.uow_factory, **kwargs)


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
