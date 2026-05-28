"""DenseFetcherStep — vector-search candidate generation (AC-17).

Mirrors :class:`ChunkFetcherStep` on the dense side:

- Reads ``state.scratch["pre_filter.result"]`` (written by
  :class:`PreFilterStep` upstream) for the parsed filter tree.
- Embeds ``state.query.terms`` via the injected :class:`Embedder`.
- Calls ``store.vector_search(query_vector, limit, filter=...)`` —
  :class:`TurboQuantVectorStore` builds the ``uint64`` allowlist from the
  filter via its :class:`CandidateIdResolver` so the ANN search is
  pre-restricted to metadata-approved rows.
- Writes the resulting :class:`Chunk` tuple to ``state.candidates`` as a
  :class:`ChunkList` — the downstream pipeline (TopK / scorer / RRF) is
  identical to the BM25 branch.

Multi-vector embedders (ColBERT-style ``list[np.ndarray]``) collapse to
``query_vec[0]`` at the boundary — :class:`TurboQuantUnitOfWork` only
persists single-vector embeddings, so a multi-vector query has nothing
to match against today. The check exists so a future PR that adds
multi-vector persistence can flip the behaviour without changing the
contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from pydocs_mcp.models import ChunkList, is_multi_vector
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.retrieval.steps.pre_filter import PRE_FILTER_SCRATCH_KEY, PreFilterResult
from pydocs_mcp.storage.protocols import Embedder, VectorSearchable

# WHY: single source of truth for the fetch-side default — referenced by
# the dataclass field, ``to_dict`` (omit-when-default), and ``from_dict``
# (fallback when YAML omits the key). Per CLAUDE.md §"Default values:
# single source of truth".
_DEFAULT_LIMIT = 50


@step_registry.register("dense_fetcher")
@dataclass(frozen=True, slots=True)
class DenseFetcherStep(RetrieverStep):
    """Dense-side candidate generation via :class:`VectorSearchable`.

    Reads ``state.query.terms`` (embedded into a vector) and the typed
    :class:`PreFilterResult` from ``state.scratch["pre_filter.result"]``.
    Writes ``state.candidates`` as a :class:`ChunkList` of vector hits
    stamped with the index score as ``relevance`` and ``retriever_name``
    (set by the store) for downstream RRF / scoring.
    """

    store: VectorSearchable
    embedder: Embedder
    limit: int = _DEFAULT_LIMIT
    name: str = field(default="dense_fetcher", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        query_text = state.query.terms.strip()
        if not query_text:
            # Empty-terms guard mirrors ChunkFetcherStep's "_build_fts_match_query
            # returned None" branch — yield an empty candidate list instead of
            # round-tripping a zero-information query through the embedder.
            return replace(state, candidates=ChunkList(items=()))

        query_vec = await self.embedder.embed_query(query_text)
        if is_multi_vector(query_vec):
            # Multi-vector embedders (ColBERT) return list[np.ndarray]. The
            # current TurboQuant persistence layer stores single vectors only
            # (see TurboQuantUnitOfWork.add_vectors), so collapsing to the
            # first token-vector keeps the contract honest until multi-vector
            # persistence lands.
            query_vec = query_vec[0]

        # Silent-None fallback: when ``state.query.pre_filter`` is set but the
        # upstream PreFilterStep hasn't published a typed result to scratch,
        # we fall back to ``filter=None`` rather than raising. This matches
        # the ChunkFetcherStep convention — pipelines that genuinely need a
        # filter should compose ``pre_filter`` before ``dense_fetcher`` (see
        # pipelines/chunk_search.yaml). The store still receives the call;
        # it just sees the unrestricted candidate set.
        filter_tree = None
        if state.query.pre_filter is not None:
            result = state.scratch.get(PRE_FILTER_SCRATCH_KEY)
            if isinstance(result, PreFilterResult):
                filter_tree = result.tree

        candidates = await self.store.vector_search(
            query_vector=query_vec,
            limit=self.limit,
            filter=filter_tree,
        )
        return replace(state, candidates=ChunkList(items=candidates))

    @classmethod
    def from_dict(cls, data: Mapping, context: BuildContext) -> DenseFetcherStep:
        if context.vector_store is None or context.embedder is None:
            raise ValueError(
                "DenseFetcherStep requires BuildContext.vector_store + "
                "BuildContext.embedder; provide both at server/CLI startup "
                "via build_retrieval_context(...) (spec AC-17).",
            )
        return cls(
            store=context.vector_store,  # type: ignore[arg-type]
            embedder=context.embedder,
            limit=data.get("limit", _DEFAULT_LIMIT),
        )

    def to_dict(self) -> dict:
        d: dict = {"type": "dense_fetcher"}
        if self.limit != _DEFAULT_LIMIT:
            d["limit"] = self.limit
        return d


__all__ = ("DenseFetcherStep",)
