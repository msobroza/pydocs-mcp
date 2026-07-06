"""ToolRouter — the six task-shaped tools over the multi-project layer (spec §D1).

One method per tool; every response is produced inside the shared
ResponseEnvelope (freshness header, pointer resolution, truncation footer).
Bodies delegate to the slice-1 router internals (_search_body/_lookup_body)
so ranking/dedup/project-routing stay in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.formatting import format_overview_card, pointer_token
from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    LookupInput,
    OverviewInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
    WhyInput,
)
from pydocs_mcp.application.multi_project_search import (
    EMPTY_SEARCH_MESSAGES,
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
    _select_service,
)
from pydocs_mcp.application.overview_service import OverviewService

# get_symbol depth → lookup `show`. The "source" depth is handled before this
# map (verbatim source path), so only "summary"/"tree" reach it. The Literal
# value type lets mypy narrow into LookupInput.show without an ignore.
_DEPTH_TO_SHOW: dict[str, Literal["default", "tree"]] = {
    "summary": "default",
    "tree": "tree",
}


@dataclass(frozen=True, slots=True)
class ToolRouter:
    services: tuple[ProjectServices, ...]
    envelope: ResponseEnvelope
    search_router: MultiProjectSearch  # constructed WITHOUT envelope; bodies only
    lookup_router: MultiProjectLookup  # constructed WITHOUT envelope; bodies only

    def _svc(self, project: str) -> ProjectServices:
        if project:
            return _select_service(self.services, project)
        return self.services[0]

    async def search_codebase(self, payload: SearchInput) -> str:
        async def _body() -> str:
            body = await self.search_router._search_body(payload)
            # Zero hits still return success (search never raises); steer the
            # agent to an orientation card via the overview pointer (spec §D1
            # empty contract). The envelope resolves the token per surface.
            if body in EMPTY_SEARCH_MESSAGES:
                return f"{body}\n{pointer_token('overview', '')}"
            return body

        return await self.envelope.wrap(_body)

    async def get_symbol(self, payload: SymbolInput) -> str:
        if payload.depth == "source":
            svc = self._svc(payload.project)
            return await self.envelope.wrap(lambda: svc.symbol_source.source_for(payload.target))
        body = LookupInput(
            target=payload.target,
            show=_DEPTH_TO_SHOW[payload.depth],
            project=payload.project,
        )
        return await self.envelope.wrap(lambda: self.lookup_router._lookup_body(body))

    async def get_references(self, payload: ReferencesInput) -> str:
        body = LookupInput(
            target=payload.target,
            show=payload.direction,
            project=payload.project,
            limit=payload.limit,
        )
        return await self.envelope.wrap(lambda: self.lookup_router._lookup_body(body))

    async def get_context(self, payload: ContextInput) -> str:
        async def _cards() -> str:
            cards = []
            for target in payload.targets:
                body = LookupInput(target=target, show="context", project=payload.project)
                cards.append(await self.lookup_router._lookup_body(body))
            return "\n\n".join(cards)

        return await self.envelope.wrap(_cards)

    async def get_why(self, payload: WhyInput) -> str:
        svc = self._svc(payload.project)

        async def _body() -> str:
            if payload.query and payload.targets:
                # §D11 both-set mode: targets filtered by query — the Null
                # service raises either way; slice 3 implements the filter.
                return await svc.decisions.for_targets(list(payload.targets))
            if payload.query:
                return await svc.decisions.search(payload.query)
            if payload.targets:
                return await svc.decisions.for_targets(list(payload.targets))
            return await svc.decisions.dashboard()

        return await self.envelope.wrap(_body)

    async def get_overview(self, payload: OverviewInput) -> str:
        svc = self._svc(payload.project)
        return await self.envelope.wrap(lambda: _render_overview(svc.overview, payload.package))


async def _render_overview(service: OverviewService, package: str) -> str:
    """Build + render the §D17 structural card. Module-level so ``get_overview``
    stays a one-liner and the service/render seam is directly testable."""
    return format_overview_card(await service.build(package))
