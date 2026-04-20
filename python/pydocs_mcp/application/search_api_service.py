"""SearchApiService — thin wrapper around member_pipeline.run (spec §5.1)."""
from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.models import ModuleMemberList, SearchQuery, SearchResponse
from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline


@dataclass(frozen=True, slots=True)
class SearchApiService:
    """Runs the module-member retrieval pipeline and wraps its state as a SearchResponse.

    Mirrors :class:`SearchDocsService` but for the module-member pipeline: the
    class is deliberately thin and substitutes an empty ``ModuleMemberList``
    when the pipeline returns no result.
    """

    member_pipeline: CodeRetrieverPipeline

    async def search(self, query: SearchQuery) -> SearchResponse:
        state = await self.member_pipeline.run(query)
        return SearchResponse(
            result=state.result or ModuleMemberList(items=()),
            query=state.query,
            duration_ms=state.duration_ms,
        )
