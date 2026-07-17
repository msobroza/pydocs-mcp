"""ToolRouter — each tool routes to the right body and stays enveloped (spec §D1)."""

import asyncio

import pytest

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    OverviewInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
    WhyInput,
)
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
)
from pydocs_mcp.application.tool_router import ToolRouter

from ._router_fakes import (
    FakeSymbolSource,
    make_envelope,
    make_project,
    make_service,
    make_services,
)


def _tool_router() -> ToolRouter:
    """A ToolRouter over the shared wiring-test fakes with a static envelope
    probe (surface="mcp"); the inner routers are built WITHOUT an envelope so
    ToolRouter owns the single wrap (bodies only)."""
    services = make_services()
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


class _FakeDecisions:
    """A DecisionNavigator whose modes echo which one ran — proving ToolRouter's
    §D11 ``get_why`` dispatch (query→search, targets→for_targets, both→filtered
    for_targets, neither→dashboard) reaches the real service path, not the Null
    raise."""

    async def search(self, query: str) -> str:
        return f"SEARCH: {query}"

    async def for_targets(self, targets: list[str], *, query: str = "") -> str:
        return f"TARGETS: {list(targets)} query={query!r}"

    async def dashboard(self) -> str:
        return "DASHBOARD"


def _tool_router_with_decisions(decisions: object) -> ToolRouter:
    """A ToolRouter wired with a real (non-Null) DecisionNavigator so the
    capture-enabled ``get_why`` path is exercisable in isolation."""
    from pydocs_mcp.application.multi_project_search import ProjectServices

    base = make_services()[0]
    services = (
        ProjectServices(
            project=make_project(),
            docs=base.docs,
            api=base.api,
            lookup=base.lookup,
            symbol_source=base.symbol_source,
            overview=base.overview,
            decisions=decisions,
        ),
    )
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


def test_search_codebase_is_enveloped_search() -> None:
    out = asyncio.run(_tool_router().search_codebase(SearchInput(query="x"))).text
    assert out.startswith("[index:")
    assert "[[next:" not in out


def test_symbol_summary_and_tree_route_to_lookup_body() -> None:
    out = asyncio.run(_tool_router().get_symbol(SymbolInput(target="pkg.mod.X"))).text
    assert out.startswith("[index:")


def test_symbol_source_routes_to_symbol_source_service() -> None:
    out = asyncio.run(
        _tool_router().get_symbol(SymbolInput(target="pkg.mod.X", depth="source"))
    ).text
    assert "```python" in out


def test_context_renders_one_card_per_target() -> None:
    out = asyncio.run(
        _tool_router().get_context(ContextInput(targets=["pkg.mod.A", "pkg.mod.B"]))
    ).text
    assert out.count("# Context for") == 2


def test_references_maps_direction_to_show() -> None:
    out = asyncio.run(
        _tool_router().get_references(ReferencesInput(target="pkg.mod.f", direction="impact"))
    ).text
    assert "Impact of" in out


def test_why_raises_service_unavailable_when_capture_disabled() -> None:
    # The shared fakes wire NullDecisionService (capture-disabled deployment);
    # ``get_why`` raises the YAML-anchored error.
    with pytest.raises(ServiceUnavailableError, match="decision_capture"):
        asyncio.run(_tool_router().get_why(WhyInput(query="why")))


def test_why_query_routes_to_real_search() -> None:
    router = _tool_router_with_decisions(_FakeDecisions())
    out = asyncio.run(router.get_why(WhyInput(query="why sqlite"))).text
    assert "SEARCH: why sqlite" in out


def test_why_targets_route_to_for_targets() -> None:
    router = _tool_router_with_decisions(_FakeDecisions())
    out = asyncio.run(router.get_why(WhyInput(targets=["pkg.mod"]))).text
    assert "TARGETS: ['pkg.mod'] query=''" in out


def test_why_query_and_targets_filter_targets_by_query() -> None:
    router = _tool_router_with_decisions(_FakeDecisions())
    out = asyncio.run(router.get_why(WhyInput(query="sidecar", targets=["pkg.mod"]))).text
    assert "TARGETS: ['pkg.mod'] query='sidecar'" in out


def test_why_neither_routes_to_dashboard() -> None:
    router = _tool_router_with_decisions(_FakeDecisions())
    out = asyncio.run(router.get_why(WhyInput())).text
    assert "DASHBOARD" in out


def test_overview_renders_structural_card() -> None:
    out = asyncio.run(_tool_router().get_overview(OverviewInput())).text
    # Enveloped (freshness header first), then the §D17 card: H1 + stats +
    # the four H2 blocks rendered from the fake service's OverviewCard.
    assert out.startswith("[index:")
    assert "# Overview — __project__" in out
    assert "## Module map" in out and "## Entry points" in out
    assert "## Structure communities" in out and "## Dependency profile" in out


def _workspace_router() -> ToolRouter:
    """A ToolRouter over TWO loaded projects — the multi-repo workspace shape."""
    services = (
        make_service("backend", package_count=3, indexed_at=2.0),
        make_service("frontend", package_count=1, indexed_at=1.0),
    )
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


def test_overview_empty_selector_multi_project_renders_workspace_card() -> None:
    out = asyncio.run(_workspace_router().get_overview(OverviewInput())).text
    assert out.startswith("[index:")
    assert "# Workspace overview" in out
    assert "**backend** — 3 packages" in out and "**frontend** — 1 packages" in out
    # Each project line deepens into its own §D17 card (envelope-resolved).
    assert '→ get_overview(project="backend")' in out
    assert '→ get_overview(project="frontend")' in out
    # The first project's card must NOT masquerade as the whole workspace.
    assert "# Overview — __project__" not in out


def test_overview_project_selector_bypasses_workspace_card() -> None:
    out = asyncio.run(_workspace_router().get_overview(OverviewInput(project="frontend"))).text
    assert "# Overview — __project__" in out
    assert "# Workspace overview" not in out


def test_overview_package_mode_bypasses_workspace_card() -> None:
    # An explicit package request keeps the §D17 per-project card — the
    # workspace card only replaces the fully-empty selector.
    out = asyncio.run(_workspace_router().get_overview(OverviewInput(package="fastapi"))).text
    assert "# Overview — fastapi" in out
    assert "# Workspace overview" not in out


def test_symbol_source_depth_resolves_via_recency_across_projects() -> None:
    """depth='source' must use the SAME recency-loop resolution as
    depth='summary'/'tree' (spec §D7 recovery chain).

    Two projects loaded, no project= selector. The target is indexed ONLY in
    "frontend" — the SECOND-loaded project but the MOST-RECENTLY-indexed one
    (higher ``indexed_at``). ``_svc('')`` naively returns ``services[0]``
    ("backend") unconditionally; a target present only in "frontend" must
    still resolve when depth='source', exactly as it already does for
    depth='summary' (which goes through ``_resolve_by_recency``).
    """
    target = "pkg.mod.OnlyInFrontend"
    services = (
        make_service(
            "backend",
            indexed_at=1.0,
            symbol_source=FakeSymbolSource(known_targets=frozenset()),
        ),
        make_service(
            "frontend",
            indexed_at=2.0,
            symbol_source=FakeSymbolSource(known_targets=frozenset({target})),
        ),
    )
    router = ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )

    # Sanity check: depth='summary' already resolves cross-project via the
    # recency loop (FakeLookup answers unconditionally regardless of project).
    summary_out = asyncio.run(router.get_symbol(SymbolInput(target=target, depth="summary"))).text
    assert target in summary_out

    # The actual gap: depth='source' must resolve the SAME target instead of
    # hard-querying services[0] ("backend", which doesn't know this symbol).
    source_out = asyncio.run(router.get_symbol(SymbolInput(target=target, depth="source"))).text
    assert "```python" in source_out
    assert f"`{target}`" in source_out
