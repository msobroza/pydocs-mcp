"""DocsSearch — thin wrapper around chunk_pipeline.run (spec §5.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar, cast

from pydocs_mcp.application.branch_search import ChunkBranchHydrator, NullChunkBranchHydrator
from pydocs_mcp.models import Chunk, ChunkList, PipelineResultItem, SearchQuery, SearchResponse
from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
from pydocs_mcp.retrieval.steps import rows_dropped_by_limit

_Rows = TypeVar("_Rows", bound=PipelineResultItem | None)


@dataclass(frozen=True, slots=True)
class DocsSearch:
    """Runs the chunk retrieval pipeline and wraps its state as a SearchResponse.

    Deliberately thin: all ranking/filtering logic lives in the pipeline
    stages. This class only threads query → pipeline → response.

    Reads ``state.result`` first (composite output from
    ``token_budget_formatter`` — what ``chunk_search.yaml`` produces) and
    falls back to ``state.candidates`` (ranked top-K from
    ``chunk_search_ranked.yaml``-style presets that omit the formatter).
    Avoids the silent-empty-results footgun if a deployment overlays the
    ranked preset onto the MCP server.

    NOTE (spec S21): this class and :class:`ApiSearch` are intentionally
    near-duplicate thin wrappers. Keeping them as two separate classes
    instead of one parameterized ``PipelineSearchService`` is deliberate
    — the duplication is grep-friendly (a developer looking for "where
    do chunk searches happen" finds exactly :class:`DocsSearch`) and
    avoids over-parameterization. If a third near-identical service
    appears in the future, that's the trigger to parameterize into a
    shared base class — not now.
    """

    chunk_pipeline: CodeRetrieverPipeline
    # Spec §6.4 (#312): a pinned query's rows take the selected branch's spans
    # — whichever step produced them. The Null default serves the services
    # composed without a bundle (tests, harnesses); pins never reach them.
    branch_hydrator: ChunkBranchHydrator = field(default_factory=NullChunkBranchHydrator)

    async def search(self, query: SearchQuery) -> SearchResponse:
        state = await self.chunk_pipeline.run(query)
        candidates = await self._on_branch(state.candidates, query.branch)
        if state.result is not None:
            result = await self._on_branch(state.result, query.branch)
        else:
            # Ranked preset (no token_budget_formatter) — surface the
            # candidates (hydrated once, #312) instead of a silent empty.
            result = candidates if candidates is not None else ChunkList(items=())
        return SearchResponse(
            result=result,
            query=state.query,
            duration_ms=state.duration_ms,
            # Ranked rows ride along so items[] (contract §3.2) come from the
            # SAME pipeline run the composite body was rendered from.
            candidates=candidates,
            # …and so does what the limit step cut, which no surviving row can
            # show (#271): the caller marks the listing partial from this.
            dropped_by_limit=rows_dropped_by_limit(state),
        )

    async def ranked(self, query: SearchQuery) -> ChunkList:
        """Return the RANKED candidate chunks (pre composite collapse).

        Multi-repo union needs item-level candidates — each chunk's per-item
        ``relevance`` score and ``package`` / ``qualified_name`` metadata — to
        merge and dedup across databases, which the single composite ``search``
        output cannot provide. Reads ``state.candidates``; falls back to
        ``state.result`` for a ranked preset that skips the formatter.
        """
        state = await self.chunk_pipeline.run(query)
        # A chunk pipeline's candidates / result are always ChunkList; the state
        # field is a union across pipeline kinds, so narrow explicitly.
        candidates = state.candidates if state.candidates is not None else state.result
        ranked = cast("ChunkList", candidates) if candidates is not None else ChunkList(items=())
        return await self._on_branch(ranked, query.branch)

    async def _on_branch(self, rows: _Rows, branch: str) -> _Rows:
        """``rows`` as ``branch`` holds them (spec §6.4, #312); an unpinned
        query's rows come back as the very same object."""
        if not branch or not isinstance(rows, ChunkList):
            return rows
        items: tuple[Chunk, ...] = await self.branch_hydrator.hydrate_on_branch(rows.items, branch)
        return cast("_Rows", ChunkList(items=items))
