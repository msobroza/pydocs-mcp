"""DocsSearch — thin wrapper around chunk_pipeline.run (spec §5.1)."""
from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.models import ChunkList, SearchQuery, SearchResponse
from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline


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

    async def search(self, query: SearchQuery) -> SearchResponse:
        state = await self.chunk_pipeline.run(query)
        result = state.result
        if result is None:
            # Ranked preset (no token_budget_formatter) — surface
            # state.candidates instead of returning silent empty.
            result = state.candidates if state.candidates is not None else ChunkList(items=())
        return SearchResponse(
            result=result,
            query=state.query,
            duration_ms=state.duration_ms,
        )
