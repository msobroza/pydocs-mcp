"""ApiSearch — thin wrapper around member_pipeline.run (spec §5.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from pydocs_mcp.models import ModuleMemberList, SearchQuery, SearchResponse
from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline


@dataclass(frozen=True, slots=True)
class ApiSearch:
    """Runs the module-member retrieval pipeline and wraps its state as a SearchResponse.

    Mirrors :class:`DocsSearch` but for the module-member pipeline:
    deliberately thin and substitutes an empty ``ModuleMemberList`` when
    the pipeline returns no result.

    NOTE (spec S21): this class and :class:`DocsSearch` are intentionally
    near-duplicate thin wrappers — see the longer note on
    :class:`DocsSearch` for the rationale. Briefly: the duplication is
    grep-friendly and avoids over-parameterization; a third near-identical
    service is the trigger to factor out a shared base, not the second.
    """

    member_pipeline: CodeRetrieverPipeline

    async def search(self, query: SearchQuery) -> SearchResponse:
        state = await self.member_pipeline.run(query)
        return SearchResponse(
            result=state.result or ModuleMemberList(items=()),
            query=state.query,
            duration_ms=state.duration_ms,
            # Ranked member rows ride along for items[] (contract §3.2) — the
            # formatter collapses ``result`` to a composite CHUNK, so the
            # per-member rows only survive here.
            candidates=state.candidates,
        )

    async def ranked(self, query: SearchQuery) -> ModuleMemberList:
        """Return the RANKED candidate members (pre composite collapse).

        Mirrors :meth:`DocsSearch.ranked` — multi-repo union needs per-item
        members (score + ``package`` / ``module`` / ``name`` metadata) to merge
        and dedup across databases.
        """
        state = await self.member_pipeline.run(query)
        # A member pipeline's candidates / result are always ModuleMemberList; the
        # state field is a union across pipeline kinds, so narrow explicitly.
        candidates = state.candidates if state.candidates is not None else state.result
        return (
            cast("ModuleMemberList", candidates)
            if candidates is not None
            else ModuleMemberList(items=())
        )
