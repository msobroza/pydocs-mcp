"""ToolRouter — each tool routes to the right body and stays enveloped (spec §D1)."""

import asyncio

import pytest

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    GlobInput,
    GrepInput,
    OverviewInput,
    ReadFileInput,
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
    FakeFileTools,
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
    §D11 ``get_why`` dispatch (query→why_search, targets→why_targets,
    both→filtered why_targets, neither→why_dashboard) reaches the real service
    path, not the Null raise. The triple methods carry a marker items row so
    the §3.6 items[] propagation is assertable at the router seam."""

    _ITEMS = (
        {
            "decision_id": 7,
            "title": "t",
            "status": "active",
            "locators": [],
            "affected_files": [],
        },
    )

    async def why_search(self, query: str):
        return f"SEARCH: {query}", self._ITEMS, {}

    async def why_targets(self, targets: list[str], *, query: str = ""):
        return f"TARGETS: {list(targets)} query={query!r}", self._ITEMS, {}

    async def why_dashboard(self):
        return "DASHBOARD", self._ITEMS, {}


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


def test_search_codebase_items_carry_chunk_rows() -> None:
    # FakeDocs answers with one non-composite chunk; the §3.2 row mirrors its
    # metadata (no source span seeded -> null path/lines, relevance None -> 0.0).
    resp = asyncio.run(_tool_router().search_codebase(SearchInput(query="x", kind="docs")))
    assert resp.items == (
        {
            "kind": "chunk",
            "id": "",
            "qualified_name": "pkg.mod.X",
            "package": "",
            "path": None,
            "start_line": None,
            "end_line": None,
            "score": 0.0,
        },
    )


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


def test_context_items_one_row_per_target_in_order() -> None:
    # §3.4: one row per resolved target, in the client's targets order —
    # the row is whatever the lookup seam resolved for the focus node.
    resp = asyncio.run(_tool_router().get_context(ContextInput(targets=["pkg.mod.A", "pkg.mod.B"])))
    assert [i["qualified_name"] for i in resp.items] == ["pkg.mod.A", "pkg.mod.B"]
    assert all(
        set(i) == {"qualified_name", "kind", "path", "start_line", "end_line"} for i in resp.items
    )


def test_symbol_source_emits_one_item_row() -> None:
    # §3.3: depth="source" carries exactly one row for the rendered span.
    resp = asyncio.run(_tool_router().get_symbol(SymbolInput(target="pkg.mod.X", depth="source")))
    assert len(resp.items) == 1
    assert resp.items[0]["qualified_name"] == "pkg.mod.X"


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


def test_why_items_propagate_to_the_envelope() -> None:
    # The §3.6 rows the DecisionNavigator triple methods return must ride the
    # envelope unchanged (Task 8) — every dispatch mode shares the seam.
    router = _tool_router_with_decisions(_FakeDecisions())
    response = asyncio.run(router.get_why(WhyInput(query="why sqlite")))
    assert response.items == _FakeDecisions._ITEMS


def test_overview_renders_structural_card() -> None:
    out = asyncio.run(_tool_router().get_overview(OverviewInput())).text
    # Enveloped (freshness header first), then the §D17 card: H1 + stats +
    # the four H2 blocks rendered from the fake service's OverviewCard.
    assert out.startswith("[index:")
    assert "# Overview — __project__" in out
    assert "## Module map" in out and "## Entry points" in out
    assert "## Structure communities" in out and "## Dependency profile" in out


def test_overview_items_mirror_module_map_rows() -> None:
    # FakeOverview's card has one ModuleEntry("pkg.mod", ...) with no node
    # provenance (defaulted node_id/source_path) -> id falls back to the
    # qualified name and path degrades to null (contract §3.1).
    resp = asyncio.run(_tool_router().get_overview(OverviewInput()))
    assert resp.items == (
        {"kind": "module", "id": "pkg.mod", "qualified_name": "pkg.mod", "path": None},
    )


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


def test_workspace_overview_emits_no_items() -> None:
    # The multi-repo workspace orientation card has no module-map rows —
    # items[] cover §3.1 module rows only (per-project deepening carries them).
    resp = asyncio.run(_workspace_router().get_overview(OverviewInput()))
    assert resp.items == ()


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


# ── grep / glob / read_file (contract §3.7-3.9) ────────────────────────────


def _files_router(files: FakeFileTools) -> ToolRouter:
    services = (make_service(files=files),)
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


def test_grep_routes_to_files_service_and_is_enveloped() -> None:
    files = FakeFileTools()
    payload = GrepInput(pattern="x")
    resp = asyncio.run(_files_router(files).grep(payload))
    assert resp.text.startswith("[index:")
    assert "GREP-BODY solo" in resp.text
    assert files.calls == [("grep", payload)]
    assert resp.items == ({"path": "a.py", "start_line": 1, "end_line": 1, "text": "x"},)
    assert resp.meta["tool"] == "grep"


def test_glob_routes_to_files_service_and_is_enveloped() -> None:
    files = FakeFileTools()
    payload = GlobInput(pattern="*.py")
    resp = asyncio.run(_files_router(files).glob(payload))
    assert resp.text.startswith("[index:")
    assert "GLOB-BODY solo" in resp.text
    assert files.calls == [("glob", payload)]
    assert resp.items == ({"path": "a.py", "mtime": 1.0},)
    assert resp.meta["tool"] == "glob"


def test_read_file_routes_to_files_service_and_is_enveloped() -> None:
    files = FakeFileTools()
    payload = ReadFileInput(file_path="a.py")
    resp = asyncio.run(_files_router(files).read_file(payload))
    assert resp.text.startswith("[index:")
    assert "READ-BODY solo" in resp.text
    assert files.calls == [("read_file", payload)]
    assert resp.items == ({"path": "a.py", "start_line": 1, "end_line": 2},)
    assert resp.meta["tool"] == "read_file"


def test_files_project_selector_routes_to_that_projects_service() -> None:
    """project= must select THAT project's FileToolsService — the filesystem
    tools serve per-project source trees, so cross-project fallback would
    answer from the wrong checkout."""
    backend_files, frontend_files = FakeFileTools("backend"), FakeFileTools("frontend")
    services = (
        make_service("backend", indexed_at=2.0, files=backend_files),
        make_service("frontend", indexed_at=1.0, files=frontend_files),
    )
    router = ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )
    resp = asyncio.run(router.grep(GrepInput(pattern="x", project="frontend")))
    assert "GREP-BODY frontend" in resp.text
    assert resp.meta["project"] == "frontend"
    assert backend_files.calls == []


def test_files_default_is_read_only_bundle_service() -> None:
    """``ProjectServices`` without explicit ``files`` wiring defaults to the
    root-less service: project-scope filesystem calls raise the typed
    read-only-bundle error instead of AttributeError-ing."""
    router = _tool_router()
    with pytest.raises(ServiceUnavailableError, match="read-only"):
        asyncio.run(router.grep(GrepInput(pattern="x")))
