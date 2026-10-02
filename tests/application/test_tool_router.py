"""ToolRouter — each tool routes to the right body and stays enveloped (spec §D1)."""

import asyncio
from dataclasses import replace

import pytest

from pydocs_mcp.application.mcp_errors import NotFoundError, ServiceUnavailableError
from pydocs_mcp.application.target_resolution import TargetResolution, TargetRewrite
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk, ChunkList, SearchResponse
from tests._fakes import FakeTargetResolver
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
from pydocs_mcp.application.tool_response import ToolResponse
from pydocs_mcp.application.tool_router import ToolRouter

from ._router_fakes import (
    FakeFileTools,
    FakeLookup,
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

    async def why_search(self, query: str, *, branch: str | None = None):
        return f"SEARCH: {query}", self._ITEMS, {}

    async def why_targets(self, targets: list[str], *, query: str = "", branch: str | None = None):
        return f"TARGETS: {list(targets)} query={query!r}", self._ITEMS, {}

    async def why_dashboard(self, *, branch: str | None = None):
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


# A miss the multi-project lookup would raise: the raw search token rides in
# the message and is resolved on the way out (envelope.py).
_GONE_MISS = "'pkg.mod.Gone' not found in any loaded project. [[next:search:Gone]]"


def _context_router(
    context_errors: dict[str, Exception], *, surface: str = "mcp", pointers_enabled: bool = True
) -> ToolRouter:
    """A one-project router whose lookup raises ``context_errors[target]``."""
    services = (replace(make_service(), lookup=FakeLookup(context_errors=context_errors)),)
    return ToolRouter(
        services=services,
        envelope=make_envelope(surface, pointers_enabled=pointers_enabled),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


def _context(router: ToolRouter, *targets: str) -> ToolResponse:
    return asyncio.run(router.get_context(ContextInput(targets=list(targets))))


def test_context_batch_answers_for_the_targets_that_resolve() -> None:
    # ADR 0023 (h): one unresolvable target no longer sinks the whole batch.
    router = _context_router({"pkg.mod.Gone": NotFoundError(_GONE_MISS)})
    resp = _context(router, "pkg.mod.A", "pkg.mod.Gone", "pkg.mod.B")
    assert [i["qualified_name"] for i in resp.items] == ["pkg.mod.A", "pkg.mod.B"]
    assert resp.text.count("# Context for pkg.mod.") == 2


def test_context_batch_renders_each_miss_before_the_cards() -> None:
    router = _context_router({"pkg.mod.Gone": NotFoundError(_GONE_MISS)})
    text = _context(router, "pkg.mod.A", "pkg.mod.Gone").text
    miss = (
        "# Context for `pkg.mod.Gone` — not indexed\n"
        "'pkg.mod.Gone' not found in any loaded project. → search_codebase(query=\"Gone\")\n"
    )
    assert miss in text
    assert text.index(miss) < text.index("# Context for pkg.mod.A")


def test_context_miss_pointer_resolves_in_cli_form() -> None:
    router = _context_router({"pkg.mod.Gone": NotFoundError(_GONE_MISS)}, surface="cli")
    text = _context(router, "pkg.mod.A", "pkg.mod.Gone").text
    assert 'not found in any loaded project. → pydocs-mcp search "Gone"\n' in text


def test_context_miss_pointer_is_stripped_when_pointers_are_disabled() -> None:
    router = _context_router({"pkg.mod.Gone": NotFoundError(_GONE_MISS)}, pointers_enabled=False)
    text = _context(router, "pkg.mod.A", "pkg.mod.Gone").text
    assert "'pkg.mod.Gone' not found in any loaded project.\n" in text
    assert "[[next:" not in text and "search_codebase" not in text


def test_context_miss_reuses_the_lookup_message_verbatim() -> None:
    # The single-project miss carries its closest names and no token; the
    # block quotes it exactly as the single-target error would have.
    message = "'pkg.mod.Gne' not found in pkg.mod. Closest indexed names: pkg.mod.Gone."
    router = _context_router({"pkg.mod.Gne": NotFoundError(message)})
    text = _context(router, "pkg.mod.Gne", "pkg.mod.A").text
    assert f"# Context for `pkg.mod.Gne` — not indexed\n{message}\n" in text


def test_context_budget_splits_over_the_resolved_targets_only() -> None:
    router = _context_router({"pkg.mod.Gone": NotFoundError(_GONE_MISS)})
    partial = _context(router, "pkg.mod.A", "pkg.mod.Gone").text
    solo = _context(router, "pkg.mod.A").text
    assert "ctx body (1 nodes, 2048 tokens)" in partial
    assert partial.endswith(solo.split("\n\n", 1)[1])


def test_context_batch_where_every_target_misses_raises_the_first_miss() -> None:
    first = NotFoundError("'pkg.mod.X' not found in pkg.mod")
    router = _context_router({"pkg.mod.X": first, "pkg.mod.Y": NotFoundError("y gone")})
    with pytest.raises(NotFoundError) as excinfo:
        _context(router, "pkg.mod.X", "pkg.mod.Y")
    assert excinfo.value is first
    assert str(excinfo.value) == "'pkg.mod.X' not found in pkg.mod"


def test_context_batch_where_every_target_misses_resolves_the_error_pointer() -> None:
    router = _context_router({"pkg.mod.Gone": NotFoundError(_GONE_MISS)})
    with pytest.raises(NotFoundError, match=r'→ search_codebase\(query="Gone"\)$'):
        _context(router, "pkg.mod.Gone")


def test_context_batch_catches_only_not_found() -> None:
    # A disabled reference graph is a deployment error, not a missing target:
    # it still fails the call even though another target resolved.
    router = _context_router({"pkg.mod.B": ServiceUnavailableError("reference graph off")})
    with pytest.raises(ServiceUnavailableError, match="reference graph off"):
        _context(router, "pkg.mod.A", "pkg.mod.B")


class _TreeLookupForbidden:
    """A tree navigator a search hit must never reach (ADR 0023 (i))."""

    async def get_tree(self, package: str, module: str) -> None:
        raise AssertionError(f"search hit rendering looked up the tree of {package}.{module}")


class _ShortProseDocs:
    """One heading hit whose three rendered lines stand for a 21-line span."""

    async def search(self, query: object) -> SearchResponse:
        hit = Chunk(
            text="one\ntwo\nthree",
            metadata={
                "title": "Pagination",
                "qualified_name": "docs.guide.md#pagination",
                "source_path": "docs/guide.md",
                "start_line": 10,
                "end_line": 30,
            },
        )
        return SearchResponse(result=ChunkList(items=(hit,)), query=query, duration_ms=0.0)


def test_a_short_prose_hit_renders_its_window_without_a_tree_lookup() -> None:
    lookup = FakeLookup(tree_svc=_TreeLookupForbidden())
    services = (replace(make_service(), docs=_ShortProseDocs(), lookup=lookup),)
    router = ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )
    text = asyncio.run(router.search_codebase(SearchInput(query="pagination"))).text
    assert 'Then: → read_file(file_path="docs/guide.md", offset=10, limit=21)\n' in text
    assert 'depth="source"' not in text


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


# ── depth="source" target fallback (spec 2026-09-10 §2.5, AC10) ───────────

_SRC_X = "src.pkg.mod.X"
_STRIP_X = TargetRewrite("source_root_strip", "pkg.mod.X", "pkg.mod", ("X",))


def _strip_x_resolver() -> FakeTargetResolver:
    return FakeTargetResolver(resolution_by_target={_SRC_X: TargetResolution(rewrite=_STRIP_X)})


def _router_over(*services: object) -> ToolRouter:
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


@pytest.mark.parametrize("project", ["", "solo"])
def test_symbol_source_retry_pins_the_project_package(project: str) -> None:
    source = FakeSymbolSource(known_targets=frozenset({"pkg.mod.X"}))
    router = _router_over(make_service(symbol_source=source, target_resolver=_strip_x_resolver()))
    out = asyncio.run(
        router.get_symbol(SymbolInput(target=_SRC_X, depth="source", project=project))
    ).text
    assert "`pkg.mod.X`" in out
    assert source.calls == [(_SRC_X, None), ("pkg.mod.X", PROJECT_PACKAGE_NAME)]


def test_symbol_source_multi_project_retry_pins_the_project_package() -> None:
    old = FakeSymbolSource(known_targets=frozenset({"pkg.mod.X"}))
    new = FakeSymbolSource(known_targets=frozenset({"pkg.mod.X"}))
    router = _router_over(
        make_service("old", indexed_at=1.0, symbol_source=old),
        make_service("new", indexed_at=2.0, symbol_source=new, target_resolver=_strip_x_resolver()),
    )
    out = asyncio.run(router.get_symbol(SymbolInput(target=_SRC_X, depth="source"))).text
    assert "`pkg.mod.X`" in out
    assert new.calls == [(_SRC_X, None), ("pkg.mod.X", PROJECT_PACKAGE_NAME)]
    assert old.calls == [(_SRC_X, None)]


def test_symbol_source_miss_appends_candidates_after_the_search_pointer() -> None:
    resolver = FakeTargetResolver(
        resolution_by_target={
            "pkg.mod.Y": TargetResolution(candidates=("pkg.mod.X",), candidate_total=1)
        }
    )
    source = FakeSymbolSource(known_targets=frozenset({"pkg.mod.X"}))
    router = _router_over(make_service(symbol_source=source, target_resolver=resolver))
    with pytest.raises(NotFoundError) as info:
        asyncio.run(router.get_symbol(SymbolInput(target="pkg.mod.Y", depth="source")))
    assert str(info.value) == (
        "'pkg.mod.Y' has no indexed source. "
        '→ search_codebase(query="pkg.mod.Y") '
        "Closest indexed names: pkg.mod.X."
    )
