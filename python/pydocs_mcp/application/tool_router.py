"""ToolRouter — the six task-shaped tools over the multi-project layer (spec §D1).

One method per tool; every response is produced inside the shared
ResponseEnvelope (freshness header, pointer resolution, truncation footer).
Bodies delegate to the slice-1 router internals (_search_body/_lookup_body)
so ranking/dedup/project-routing stay in exactly one place.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

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
from pydocs_mcp.application.overview_service import (
    OverviewCard,
    OverviewService,
    WorkspaceProjectEntry,
)
from pydocs_mcp.application.tool_response import ToolResponse

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
    # Workspace cross-link freshness for the get_overview card (spec §3.8):
    # "" (single project / not composed) renders nothing — byte-identical.
    cross_link_status: str = ""

    def _svc(self, project: str) -> ProjectServices:
        if project:
            return _select_service(self.services, project)
        return self.services[0]

    def _meta_project(self, project: str) -> str:
        """``meta.project`` attribution (contract §2.1): the client's explicit
        selector, else the default (first-loaded) project's resolved name."""
        return project or self.services[0].project.name

    async def _resolve_source(self, target: str, project: str) -> str:
        """``depth='source'`` body — mirrors ``MultiProjectLookup._lookup_body``'s
        project-routing shape (explicit project → single service; single-project
        deployment → services[0]; otherwise resolve by recency) so a target
        indexed only in a non-first project still resolves (spec §D7)."""
        if project:
            return await self._svc(project).symbol_source.source_for(target)
        if len(self.services) == 1:
            return await self.services[0].symbol_source.source_for(target)
        return await self.lookup_router._resolve_by_recency(
            lambda svc: svc.symbol_source.source_for(target),
            target=target,
        )

    async def search_codebase(self, payload: SearchInput) -> ToolResponse:
        async def _body() -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
            body, items, extras = await self.search_router._search_body(payload)
            # Zero hits still return success (search never raises); steer the
            # agent to an orientation card via the overview pointer (spec §D1
            # empty contract). The envelope resolves the token per surface.
            if body in EMPTY_SEARCH_MESSAGES:
                return f"{body}\n{pointer_token('overview', '')}", items, extras
            return body, items, extras

        return await self.envelope.wrap(
            "search_codebase", self._meta_project(payload.project), _body
        )

    async def get_symbol(self, payload: SymbolInput) -> ToolResponse:
        if payload.depth == "source":
            # Route through the SAME project-routing / recency resolution
            # depth="summary"/"tree" use (MultiProjectLookup._resolve_by_recency)
            # instead of hard-querying services[0] — otherwise a target indexed
            # only in a NON-first project resolves for summary/tree but 404s for
            # source, breaking the §D7 truncation-card recovery pointer.
            return await self.envelope.wrap(
                "get_symbol",
                self._meta_project(payload.project),
                lambda: self._resolve_source(payload.target, payload.project),
            )
        body = LookupInput(
            target=payload.target,
            show=_DEPTH_TO_SHOW[payload.depth],
            project=payload.project,
        )
        return await self.envelope.wrap(
            "get_symbol",
            self._meta_project(payload.project),
            lambda: self.lookup_router._lookup_body(body),
        )

    async def get_references(self, payload: ReferencesInput) -> ToolResponse:
        body = LookupInput(
            target=payload.target,
            show=payload.direction,
            project=payload.project,
            limit=payload.limit,
        )
        return await self.envelope.wrap(
            "get_references",
            self._meta_project(payload.project),
            lambda: self.lookup_router._lookup_body(body),
        )

    async def get_context(self, payload: ContextInput) -> ToolResponse:
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

        return await self.envelope.wrap("get_context", self._meta_project(payload.project), _cards)

    async def get_why(self, payload: WhyInput) -> ToolResponse:
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

        return await self.envelope.wrap("get_why", self._meta_project(payload.project), _body)

    async def get_overview(self, payload: OverviewInput) -> ToolResponse:
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
            return await self.envelope.wrap(
                "get_overview",
                self._meta_project(payload.project),
                lambda: _render_workspace_overview(
                    self.services, cross_link_status=self.cross_link_status
                ),
            )
        svc = self._svc(payload.project)
        return await self.envelope.wrap(
            "get_overview",
            self._meta_project(payload.project),
            lambda: _render_overview(svc.overview, payload.package),
        )


async def _render_overview(
    service: OverviewService, package: str
) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
    """Build + render the §D17 structural card plus its §3.1 items[] rows.
    Module-level so ``get_overview`` stays a one-liner and the service/render
    seam is directly testable."""
    card = await service.build(package)
    return format_overview_card(card), _overview_items(card), {}


def _overview_items(card: OverviewCard) -> tuple[dict[str, Any], ...]:
    """One §3.1 row per module-map entry (contract: ``{kind, id,
    qualified_name, path}``; module rows carry ``path`` where resolvable).
    Entries without persisted node provenance fall back to the qualified name
    as ``id`` and a null ``path``."""
    return tuple(
        {
            "kind": entry.kind,
            "id": entry.node_id or entry.qualified_name,
            "qualified_name": entry.qualified_name,
            "path": entry.source_path or None,
        }
        for entry in card.modules
    )


async def _render_workspace_overview(
    services: tuple[ProjectServices, ...], *, cross_link_status: str = ""
) -> str:
    """Build + render the workspace orientation card (multi-repo, empty selector).

    Package counts are gathered concurrently — one light census read per loaded
    project — and rendered in loaded (workspace-glob) order so the card is
    deterministic across calls. ``cross_link_status`` appends the one-line
    workspace cross-link freshness (spec §3.8); empty renders nothing.
    """
    counts = await asyncio.gather(*[svc.overview.package_count() for svc in services])
    entries = tuple(
        WorkspaceProjectEntry(name=svc.project.name, package_count=count)
        for svc, count in zip(services, counts, strict=True)
    )
    card = format_workspace_overview_card(entries)
    if cross_link_status:
        card += f"\ncross-repo links: {cross_link_status}\n"
    return card


def _split_budget(total: int, sizes: list[int]) -> list[int]:
    """Split ``total`` tokens across cards — ONE shared budget, never exceeded.

    Reserve the per-card floor (``int(total * _MIN_SHARE_RATIO)``) for every
    card, then distribute the REMAINING budget proportionally to closure
    ``sizes`` (empty closures share the remainder evenly). This guarantees the
    invariant ``sum(shares) <= total`` while still giving a tiny closure
    batched beside a huge one its guaranteed floor.

    WHY not ``max(floor, proportional)``: that layered the floor ON TOP of an
    already-full proportional split, so any floor-bound card pushed the total
    over budget — up to ~2x with 20 equal cards (``ContextInput.targets`` caps
    at 20), and past budget for any skewed batch with a small closure. The
    floor is only affordable while ``len(sizes) * floor <= total`` (i.e. up to
    ``1/_MIN_SHARE_RATIO`` = 10 cards); beyond that the floor guarantee is
    structurally impossible, so it degrades to a strict even split of ``total``.

    Module-level + pure so the split math is unit-testable apart from the async
    two-phase orchestration in ``ToolRouter.get_context``.
    """
    n = len(sizes)
    floor = int(total * _MIN_SHARE_RATIO)
    # Floor unaffordable (> 1/ratio cards): can't honor it without overshooting,
    # so split the whole budget evenly instead.
    if n * floor > total:
        even = total // n
        return [even for _ in sizes]
    # Floor affordable: reserve it for every card, hand out the remainder
    # proportionally (empty closures -> even remainder). ``floor + remainder``
    # per card sums to at most ``total`` because the proportional parts sum to
    # at most ``remainder`` under floor division.
    remainder = total - n * floor
    denom = sum(sizes)
    if denom == 0:
        extra = remainder // n
        return [floor + extra for _ in sizes]
    return [floor + remainder * size // denom for size in sizes]
