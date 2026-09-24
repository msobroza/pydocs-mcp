"""ToolRouter — the nine task-shaped tools over the multi-project layer (spec §D1).

One method per tool; every response is produced inside the shared
ResponseEnvelope (freshness header, pointer resolution, truncation footer),
after the request's ``branch`` selector is resolved against the named
project's branch directory and with that project's own freshness probe
(``_enveloped``; spec §6.4, O19, #311). Index-backed bodies delegate to the
slice-1 router internals (_search_body/_lookup_body) so
ranking/dedup/project-routing stay in exactly one place; the filesystem tools
(grep/glob/read_file, contract §3.7-3.9) delegate to the selected project's
FileToolsService.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any, Literal, Protocol

from pydocs_mcp.application.branch_resolution import ResolvedBranch, resolve_branch_selector
from pydocs_mcp.application.branch_search import RequestBranchPins
from pydocs_mcp.application.envelope import BodyResult, ResponseEnvelope
from pydocs_mcp.application.formatting import (
    format_overview_card,
    format_workspace_overview_card,
)
from pydocs_mcp.application.lookup_service import TARGET_EXTENSION_EXTRA
from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    GlobInput,
    GrepInput,
    LookupInput,
    OverviewInput,
    ReadFileInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
    WhyInput,
)
from pydocs_mcp.application.multi_project_search import (
    ANSWERING_BUNDLE_EXTRA,
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
from pydocs_mcp.application.pointer_bundles import render_pointer_bundle
from pydocs_mcp.application.reference_resolution import declared_reference_resolution
from pydocs_mcp.application.suggestions import (
    SEARCH_ZERO_HIT_SUGGESTION,
    log_suggestion_fired,
)
from pydocs_mcp.application.target_resolution import TargetRewrite, with_target_fallback
from pydocs_mcp.application.tool_response import ToolResponse
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.multirepo import current_metadata
from pydocs_mcp.pointer_table import PointerTableConfig, ResponseKind
from pydocs_mcp.retrieval.config import SuggestionsConfig
from pydocs_mcp.storage.index_metadata import IndexMetadata

# The lookup body's internal extras channels (see multi_project_search /
# lookup_service). get_references consumes both; every consumer strips both
# before the wire.
_LOOKUP_CHANNEL_KEYS = frozenset({TARGET_EXTENSION_EXTRA, ANSWERING_BUNDLE_EXTRA})

# One depth="source" envelope body triple (text, §3.3 rows, meta extras).
_SourceBody = tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]

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


class _ProjectScopedInput(Protocol):
    """What the router reads off every one of the nine tool inputs."""

    @property
    def project(self) -> str: ...


def _branch_selector(payload: object) -> str:
    """The request's ``branch`` selector (spec §6.4). #315 declares the field on
    the nine inputs; until then it is ``""`` (the CLI's ``search --branch`` passes its own)."""
    return str(getattr(payload, "branch", ""))


def _without_lookup_channels(extras: dict[str, Any]) -> dict[str, Any]:
    """``extras`` minus ``_LOOKUP_CHANNEL_KEYS`` — the part that may reach the
    wire meta. One strip for both consumers, so a third channel is added once."""
    return {k: v for k, v in extras.items() if k not in _LOOKUP_CHANNEL_KEYS}


def _rewritten_source(svc: ProjectServices, rewrite: TargetRewrite) -> Awaitable[_SourceBody]:
    """The depth="source" retry, pinned to ``__project__`` (spec 2026-09-10 P2)."""
    return svc.symbol_source.source_with_items(rewrite.canonical, package=PROJECT_PACKAGE_NAME)


async def _source_with_target_fallback(svc: ProjectServices, target: str) -> _SourceBody:
    """One project's depth="source" read with the exact-first target fallback."""
    return await with_target_fallback(
        target,
        entry="source",
        resolver=svc.lookup.target_resolver,
        run_exact=lambda: svc.symbol_source.source_with_items(target),
        run_rewrite=lambda rewrite: _rewritten_source(svc, rewrite),
    )


@dataclass(frozen=True, slots=True)
class ToolRouter:
    services: tuple[ProjectServices, ...]
    envelope: ResponseEnvelope
    search_router: MultiProjectSearch  # constructed WITHOUT envelope; bodies only
    lookup_router: MultiProjectLookup  # constructed WITHOUT envelope; bodies only
    # Workspace cross-link freshness for the get_overview card (spec §3.8):
    # "" (single project / not composed) renders nothing — byte-identical.
    cross_link_status: str = ""
    # ADR 0007 per-rule flags; the router owns only the search_codebase
    # zero-hit rule (grep rules live in FileToolsService, the get_why one in
    # DecisionService — one flag, both zero-hit producer sites).
    suggestions: SuggestionsConfig = field(default_factory=SuggestionsConfig)
    # The deployment's pointer table — the only source of the follow-up calls
    # every response renders; the default is the shipped table.
    pointers: PointerTableConfig = field(default_factory=PointerTableConfig)

    def _svc(self, project: str) -> ProjectServices:
        if project:
            return _select_service(self.services, project)
        return self.services[0]

    def _meta_project(self, project: str) -> str:
        """``meta.project`` attribution (contract §2.1): the client's explicit
        selector, else the default (first-loaded) project's resolved name."""
        return project or self.services[0].project.name

    async def _resolve_branch(self, svc: ProjectServices, selector: str) -> ResolvedBranch:
        """Resolve ``selector`` against ``svc``'s bundle, once per request.

        One TTL-cached directory snapshot (plumbing reads only, never a git
        process — AC-31) and an in-memory touch (never a write on the request
        path, spec §6.4). The checkout suggestion obeys its ADR 0007 flag.
        """
        resolved = resolve_branch_selector(selector, await svc.branch_directory.snapshot())
        if resolved.name:
            svc.branch_directory.touch(resolved.name)
        if resolved.suggestion and not self.suggestions.checkout_not_indexed:
            return replace(resolved, suggestion=None)
        return resolved

    async def _enveloped(
        self,
        tool: str,
        payload: _ProjectScopedInput,
        produce: Callable[[], Awaitable[BodyResult]],
    ) -> ToolResponse:
        """Wrap ``produce`` with the branch the request resolves to and the
        freshness probe of the project ``meta.project`` names (O19, #311):
        that project's own index head and staleness, never the first one's."""
        return await self._enveloped_on_branch(
            tool, payload, lambda _: produce(), _branch_selector(payload)
        )

    async def _enveloped_on_branch(
        self,
        tool: str,
        payload: _ProjectScopedInput,
        produce: Callable[[ResolvedBranch], Awaitable[BodyResult]],
        selector: str,
    ) -> ToolResponse:
        """:meth:`_enveloped` whose ``produce`` reads the branch meta names (#312)."""
        svc = self._svc(payload.project)
        branch = await self._resolve_branch(svc, selector)
        project = self._meta_project(payload.project)
        return await self.envelope.wrap(
            tool, project, lambda: produce(branch), branch=branch, probe=svc.freshness
        )

    def _answering_service(self, extras: dict[str, Any], fallback_project: str) -> ProjectServices:
        """The project whose lookup ANSWERED, by the body's ``ANSWERING_BUNDLE_EXTRA``
        tag (a db path). Under multi-repo with no selector the answer comes from
        whichever project resolved first by recency, not necessarily the
        first-loaded one, and not necessarily the NEWEST of two projects that
        share a name — which is what resolving a bare name would pick.
        (``meta.project`` still attributes by the older explicit-else-first
        rule; a pre-existing approximation, not widened here.) A body carrying
        no tag falls back to that same rule.
        """
        tag = extras.get(ANSWERING_BUNDLE_EXTRA)
        if not tag:
            return self._svc(fallback_project)
        for svc in self.services:
            if str(svc.project.db_path) == tag:
                return svc
        loaded = [str(s.project.db_path) for s in self.services]
        raise LookupError(f"answering bundle {tag!r} is not a loaded project; loaded: {loaded}")

    async def _stamped_metadata(self, svc: ProjectServices) -> IndexMetadata:
        """``svc``'s index-time grammar stamp, as it is on disk now.

        Read at request time rather than from the load-time
        ``LoadedProject.metadata``: a separate ``index`` / ``watch`` process can
        re-stamp the bundle underneath a running server. The freshness header
        re-reads the same row under a TTL (``head_check_ttl_seconds``), so
        within one TTL window the header may still describe the previous
        pass while this value already describes the new one — never the
        reverse. Off the event loop, like every other SQLite read the
        server makes.
        """
        return await asyncio.to_thread(current_metadata, svc.project)

    async def _resolve_source(
        self, target: str, project: str
    ) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
        """``depth='source'`` body — mirrors ``MultiProjectLookup._lookup_body``'s
        project-routing shape (explicit project → single service; single-project
        deployment → services[0]; otherwise resolve by recency) so a target
        indexed only in a non-first project still resolves (spec §D7). Carries
        the one §3.3 row for the rendered span (Task 6). A miss gets the same
        target fallback as summary/tree (spec 2026-09-10 §2.5)."""
        if project or len(self.services) == 1:
            return await _source_with_target_fallback(self._svc(project), target)
        return await self.lookup_router._resolve_by_recency(
            lambda svc: svc.symbol_source.source_with_items(target),
            _rewritten_source,
            target=target,
            entry="source",
        )

    async def search_codebase(self, payload: SearchInput, *, branch: str = "") -> ToolResponse:
        answering = self._svc(payload.project)
        resolve_default = partial(self._resolve_branch, selector="")

        async def _body(resolved: ResolvedBranch) -> BodyResult:
            pins = RequestBranchPins.for_request(answering, resolved, resolve_default)
            body, items, extras = await self.search_router._search_body(payload, branch_pins=pins)
            # Zero hits still return success (search never raises); steer the
            # agent to an orientation card via the overview pointer (spec §D1
            # empty contract). The envelope resolves the token per surface.
            # Flag-gated per ADR 0007 (independent ablation of the shipped
            # hint); the fired text is mirrored machine-readably in
            # meta.suggestion (§2.3).
            if body in EMPTY_SEARCH_MESSAGES and self.suggestions.search_zero_hit:
                log_suggestion_fired("search_codebase", "search_zero_hit")
                zero_hit = self.pointers.row_for(ResponseKind.ZERO_HIT)
                return (
                    f"{body}\n{render_pointer_bundle(zero_hit, '')}",
                    items,
                    {**extras, "suggestion": SEARCH_ZERO_HIT_SUGGESTION},
                )
            return body, items, extras

        selector = branch or _branch_selector(payload)
        return await self._enveloped_on_branch("search_codebase", payload, _body, selector)

    async def get_symbol(self, payload: SymbolInput) -> ToolResponse:
        if payload.depth == "source":
            # Route through the SAME project-routing / recency resolution
            # depth="summary"/"tree" use (MultiProjectLookup._resolve_by_recency)
            # instead of hard-querying services[0] — otherwise a target indexed
            # only in a NON-first project resolves for summary/tree but 404s for
            # source, breaking the §D7 truncation-card recovery pointer.
            return await self._enveloped(
                "get_symbol",
                payload,
                lambda: self._resolve_source(payload.target, payload.project),
            )
        body = LookupInput(
            target=payload.target,
            show=_DEPTH_TO_SHOW[payload.depth],
            project=payload.project,
        )

        async def _symbol_body() -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
            # The lookup body threads TARGET_EXTENSION_EXTRA (ADR 0021 Decision
            # 6 — get_references needs it for module targets) and
            # ANSWERING_BUNDLE_EXTRA (which bundle's stamp to read) on every
            # return. Both channels are get_references-only; strip them here so
            # get_symbol's meta stays exactly its pinned field set.
            text, items, extras = await self.lookup_router._lookup_body(body)
            return text, items, _without_lookup_channels(extras)

        return await self._enveloped("get_symbol", payload, _symbol_body)

    async def get_references(self, payload: ReferencesInput) -> ToolResponse:
        body = LookupInput(
            target=payload.target,
            show=payload.direction,
            project=payload.project,
            limit=payload.limit,
        )

        async def _body() -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
            text, items, extras = await self.lookup_router._lookup_body(body)
            # §2.2 meta extension: the HONEST declared capability level for the
            # target's language (ADR 0021 Decision 6 / ADR 0022). The lookup body
            # threads the target's file extension via TARGET_EXTENSION_EXTRA; route
            # it through the analyzer registry AND the bundle's index-time grammar
            # stamp, so a target with no analyzer, or a bundle whose graph never
            # captured that language, reports "unavailable" instead of overstating
            # a structurally empty graph. Strip both internal channels so only the
            # declared `resolution` reaches the wire meta.
            ext = extras.get(TARGET_EXTENSION_EXTRA)
            answering = self._answering_service(extras, payload.project)
            forwarded = _without_lookup_channels(extras)
            resolution = declared_reference_resolution(ext, await self._stamped_metadata(answering))
            return text, items, {**forwarded, "resolution": resolution}

        return await self._enveloped("get_references", payload, _body)

    async def get_context(self, payload: ContextInput) -> ToolResponse:
        async def _cards() -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
            # Phase 1 — resolve every target's forward closure through the same
            # project-routing / recency resolution a single lookup uses.
            resolved = [
                await self.lookup_router.resolve_context(target, payload.project)
                for target in payload.targets
            ]
            # Phase 2 — split the ONE shared budget proportionally to closure
            # size, then render each card at its own share. items[] carry one
            # §3.4 row per resolved target, in the client's targets order.
            svc = self._svc(payload.project)
            budget = svc.lookup.context_token_budget
            shares = _split_budget(budget, [len(nodes) for _, nodes, _ in resolved])
            cards = [
                svc.lookup.render_context_card(target, nodes, token_budget=share)
                for (target, nodes, _), share in zip(resolved, shares, strict=True)
            ]
            items = tuple(focus_row for _, _, focus_row in resolved)
            return "\n\n".join(cards), items, {}

        return await self._enveloped("get_context", payload, _cards)

    async def get_why(self, payload: WhyInput) -> ToolResponse:
        svc = self._svc(payload.project)

        async def _body() -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
            if payload.query and payload.targets:
                # §D11 both-set mode: targets filtered by query — the Null
                # service raises either way; the real service implements the filter.
                return await svc.decisions.why_targets(list(payload.targets), query=payload.query)
            if payload.query:
                return await svc.decisions.why_search(payload.query)
            if payload.targets:
                return await svc.decisions.why_targets(list(payload.targets))
            return await svc.decisions.why_dashboard()

        return await self._enveloped("get_why", payload, _body)

    async def grep(self, payload: GrepInput) -> ToolResponse:
        # The filesystem tools are strictly per-project (they serve ONE source
        # tree, contract §4.1): empty selector = the default (first-loaded)
        # project — no cross-project recency fallback, which would silently
        # answer from a different checkout.
        svc = self._svc(payload.project)
        return await self._enveloped("grep", payload, lambda: svc.files.grep(payload))

    async def glob(self, payload: GlobInput) -> ToolResponse:
        svc = self._svc(payload.project)
        return await self._enveloped("glob", payload, lambda: svc.files.glob(payload))

    async def read_file(self, payload: ReadFileInput) -> ToolResponse:
        svc = self._svc(payload.project)
        return await self._enveloped("read_file", payload, lambda: svc.files.read_file(payload))

    async def get_overview(self, payload: OverviewInput) -> ToolResponse:
        # Fully-empty selector on a multi-repo server: routing to services[0]
        # would silently describe ONE project as if it were the whole workspace
        # — render the workspace orientation card instead (one line per loaded
        # project, deepening via get_overview(project=...)). Package mode and
        # single-project deployments keep the §D17 card unchanged.
        #
        # The envelope's [index: … · N packages] freshness header reports the
        # project meta.project names (O19, #311: each project's own probe) —
        # with no selector, the FIRST-loaded one. So it legitimately differs
        # from this card's workspace-total census; that divergence is expected,
        # not a bug to "reconcile".
        if not payload.project and not payload.package and len(self.services) > 1:
            return await self._enveloped(
                "get_overview",
                payload,
                lambda: _render_workspace_overview(
                    self.services,
                    pointers=self.pointers,
                    cross_link_status=self.cross_link_status,
                ),
            )
        svc = self._svc(payload.project)
        return await self._enveloped(
            "get_overview",
            payload,
            lambda: _render_overview(svc.overview, payload.package, self.pointers),
        )


async def _render_overview(
    service: OverviewService, package: str, pointers: PointerTableConfig
) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
    """Build + render the §D17 structural card plus its §3.1 items[] rows.
    Module-level so ``get_overview`` stays a one-liner and the service/render
    seam is directly testable."""
    card = await service.build(package)
    return format_overview_card(card, pointers=pointers), _overview_items(card), {}


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
    services: tuple[ProjectServices, ...],
    *,
    pointers: PointerTableConfig,
    cross_link_status: str = "",
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
    card = format_workspace_overview_card(entries, pointers=pointers)
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
