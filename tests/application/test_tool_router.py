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

from ._router_fakes import make_envelope, make_services


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


def test_search_codebase_is_enveloped_search() -> None:
    out = asyncio.run(_tool_router().search_codebase(SearchInput(query="x")))
    assert out.startswith("[index:")
    assert "[[next:" not in out


def test_symbol_summary_and_tree_route_to_lookup_body() -> None:
    out = asyncio.run(_tool_router().get_symbol(SymbolInput(target="pkg.mod.X")))
    assert out.startswith("[index:")


def test_symbol_source_routes_to_symbol_source_service() -> None:
    out = asyncio.run(_tool_router().get_symbol(SymbolInput(target="pkg.mod.X", depth="source")))
    assert "```python" in out


def test_context_renders_one_card_per_target() -> None:
    out = asyncio.run(_tool_router().get_context(ContextInput(targets=["pkg.mod.A", "pkg.mod.B"])))
    assert out.count("# Context for") == 2


def test_references_maps_direction_to_show() -> None:
    out = asyncio.run(
        _tool_router().get_references(ReferencesInput(target="pkg.mod.f", direction="impact"))
    )
    assert "Impact of" in out


def test_why_raises_service_unavailable() -> None:
    with pytest.raises(ServiceUnavailableError, match="decision_capture"):
        asyncio.run(_tool_router().get_why(WhyInput(query="why")))


def test_overview_renders_structural_card() -> None:
    out = asyncio.run(_tool_router().get_overview(OverviewInput()))
    # Enveloped (freshness header first), then the §D17 card: H1 + stats +
    # the four H2 blocks rendered from the fake service's OverviewCard.
    assert out.startswith("[index:")
    assert "# Overview — __project__" in out
    assert "## Module map" in out and "## Entry points" in out
    assert "## Structure communities" in out and "## Dependency profile" in out
