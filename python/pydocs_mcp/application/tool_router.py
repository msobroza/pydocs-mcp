"""ToolRouter — the six task-shaped tools over the multi-project layer (spec §D1).

One method per tool; every response is produced inside the shared
ResponseEnvelope (freshness header, pointer resolution, truncation footer).
Bodies delegate to the slice-1 router internals (_search_body/_lookup_body)
so ranking/dedup/project-routing stay in exactly one place.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.formatting import (
    format_overview_card,
    format_workspace_overview_card,
    pointer_token,
)
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
from pydocs_mcp.application.overview_service import OverviewService, WorkspaceProjectEntry

# get_symbol depth → lookup `show`. The "source" depth is handled before this
# map (verbatim source path), so only "summary"/"tree" reach it. The Literal
# value type lets mypy narrow into LookupInput.show without an ignore.
_DEPTH_TO_SHOW: dict[str, Literal["default", "tree"]] = {
    "summary": "default",
    "tree": "tree",
}

# Floor share every context card is guaranteed regardless of closure-size skew,
# so a tiny closure batched beside a huge one still renders its focus block
# (spec §D1 batched-context contract). Single source of truth for the split.
_MIN_SHARE_RATIO = 0.10


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
            # Phase 1 — resolve every target's forward closure through the same
            # project-routing / recency resolution a single lookup uses.
            resolved = [
                await self.lookup_router.resolve_context(target, payload.project)
                for target in payload.targets
            ]
            # Phase 2 — split the ONE shared budget proportionally to closure
            # size, then render each card at its own share.
            svc = self._svc(payload.project)
            budget = svc.lookup.context_token_budget
            shares = _split_budget(budget, [len(nodes) for _, nodes in resolved])
            cards = [
                svc.lookup.render_context_card(target, nodes, token_budget=share)
                for (target, nodes), share in zip(resolved, shares, strict=True)
            ]
            return "\n\n".join(cards)

        return await self.envelope.wrap(_cards)

    async def get_why(self, payload: WhyInput) -> str:
        svc = self._svc(payload.project)

        async def _body() -> str:
            if payload.query and payload.targets:
                # §D11 both-set mode: targets filtered by query — the Null
                # service raises either way; the real service implements the filter.
                return await svc.decisions.for_targets(list(payload.targets), query=payload.query)
            if payload.query:
                return await svc.decisions.search(payload.query)
            if payload.targets:
                return await svc.decisions.for_targets(list(payload.targets))
            return await svc.decisions.dashboard()

        return await self.envelope.wrap(_body)

    async def get_overview(self, payload: OverviewInput) -> str:
        # Fully-empty selector on a multi-repo server: routing to services[0]
        # would silently describe ONE project as if it were the whole workspace
        # — render the workspace orientation card instead (one line per loaded
        # project, deepening via get_overview(project=...)). Package mode and
        # single-project deployments keep the §D17 card unchanged.
        #
        # The envelope's [index: … · N packages] freshness header reports the
        # FIRST project only (the probe is built from services[0]; server.py) —
        # by design, one freshness stamp per router. So it legitimately differs
        # from this card's workspace-total census; that divergence is expected,
        # not a bug to "reconcile".
        if not payload.project and not payload.package and len(self.services) > 1:
            return await self.envelope.wrap(lambda: _render_workspace_overview(self.services))
        svc = self._svc(payload.project)
        return await self.envelope.wrap(lambda: _render_overview(svc.overview, payload.package))


async def _render_overview(service: OverviewService, package: str) -> str:
    """Build + render the §D17 structural card. Module-level so ``get_overview``
    stays a one-liner and the service/render seam is directly testable."""
    return format_overview_card(await service.build(package))


async def _render_workspace_overview(services: tuple[ProjectServices, ...]) -> str:
    """Build + render the workspace orientation card (multi-repo, empty selector).

    Package counts are gathered concurrently — one light census read per loaded
    project — and rendered in loaded (workspace-glob) order so the card is
    deterministic across calls.
    """
    counts = await asyncio.gather(*[svc.overview.package_count() for svc in services])
    entries = tuple(
        WorkspaceProjectEntry(name=svc.project.name, package_count=count)
        for svc, count in zip(services, counts, strict=True)
    )
    return format_workspace_overview_card(entries)


def _split_budget(total: int, sizes: list[int]) -> list[int]:
    """Split ``total`` tokens across cards proportionally to closure ``sizes``.

    ``share_i = max(floor, total * size_i / Σsizes)`` with
    ``floor = int(total * _MIN_SHARE_RATIO)`` — every card is guaranteed the
    floor so a tiny closure batched beside a huge one still renders (a bigger
    closure just gets proportionally more of the remaining budget). When every
    closure is empty (``Σsizes == 0``) the budget splits evenly, so each card
    still gets a usable share instead of collapsing to the floor.

    Module-level + pure so the proportional-split math is unit-testable apart
    from the async two-phase orchestration in ``ToolRouter.get_context``.
    """
    floor = int(total * _MIN_SHARE_RATIO)
    denom = sum(sizes)
    if denom == 0:
        even = total // len(sizes)
        return [max(floor, even) for _ in sizes]
    return [max(floor, total * size // denom) for size in sizes]
