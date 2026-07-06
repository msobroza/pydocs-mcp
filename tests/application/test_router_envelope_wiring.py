"""Routers wrap every response in the envelope; surfaces differ only in
pointer syntax (spec §D3/§D4)."""

import asyncio

from pydocs_mcp.application.mcp_inputs import LookupInput, SearchInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
)

from ._router_fakes import make_envelope, make_services


def _search_router(surface: str) -> MultiProjectSearch:
    return MultiProjectSearch(services=make_services(), envelope=make_envelope(surface))


def test_mcp_search_response_is_enveloped() -> None:
    out = asyncio.run(_search_router("mcp").search(SearchInput(query="x", kind="docs")))
    assert out.startswith("[index: 8e2110e")
    assert "[[next:" not in out


def test_cli_and_mcp_differ_only_in_pointer_syntax() -> None:
    mcp_out = asyncio.run(_search_router("mcp").search(SearchInput(query="x", kind="docs")))
    cli_out = asyncio.run(_search_router("cli").search(SearchInput(query="x", kind="docs")))

    def normalize(s: str) -> str:
        return s.replace('→ get_symbol(target="pkg.mod.X")', "<PTR>").replace(
            "→ pydocs-mcp symbol pkg.mod.X", "<PTR>"
        )

    assert normalize(mcp_out) == normalize(cli_out)


def test_router_without_envelope_strips_tokens() -> None:
    # Legacy construction (tests, embedders) must never leak raw tokens.
    router = MultiProjectSearch(services=make_services())
    out = asyncio.run(router.search(SearchInput(query="x", kind="docs")))
    assert "[[next:" not in out and not out.startswith("[index:")


def test_lookup_router_enveloped_too() -> None:
    router = MultiProjectLookup(services=make_services(), envelope=make_envelope("mcp"))
    out = asyncio.run(router.lookup(LookupInput(target="")))
    assert out.startswith("[index: 8e2110e")
