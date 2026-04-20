"""SearchDocsService — thin wrapper around chunk_pipeline.run (spec §5.1)."""
from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.models import ChunkList, SearchQuery, SearchResponse
from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline


@dataclass(frozen=True, slots=True)
class SearchDocsService:
    """Runs the chunk retrieval pipeline and wraps its state as a SearchResponse.

    The service is deliberately thin: all ranking/filtering logic lives in the
    pipeline stages. This class only threads query → pipeline → response and
    substitutes an empty ``ChunkList`` when the pipeline returns no result.
    """

    chunk_pipeline: CodeRetrieverPipeline

    async def search(self, query: SearchQuery) -> SearchResponse:
        state = await self.chunk_pipeline.run(query)
        return SearchResponse(
            result=state.result or ChunkList(items=()),
            query=state.query,
            duration_ms=state.duration_ms,
        )
