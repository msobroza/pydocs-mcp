"""Routers wrap every response in the envelope; surfaces differ only in
pointer syntax (spec §D3/§D4)."""

import asyncio
from pathlib import Path

from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.formatting import pointer_token
from pydocs_mcp.application.freshness import EnvelopeInfo
from pydocs_mcp.application.mcp_inputs import LookupInput, SearchInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
)
from pydocs_mcp.models import ChunkList, ModuleMemberList, SearchResponse
from pydocs_mcp.multirepo import LoadedProject
from pydocs_mcp.storage.index_metadata import IndexMetadata

SHA = "8e2110e" + "0" * 33


class _StaticProbe:
    async def envelope_info(self) -> EnvelopeInfo:
        return EnvelopeInfo(
            indexed_commit=SHA,
            live_commit=SHA,
            age_days=0,
            package_count=1,
            stale=False,
        )


class _FakeDocs:
    """A docs search whose composite hit carries a lookup pointer token for
    ``pkg.mod.X`` — mirroring what the real formatting pipeline emits."""

    async def search(self, query):
        from pydocs_mcp.models import Chunk

        text = f"## X\nbody\n{pointer_token('lookup', 'pkg.mod.X')}\n"
        item = Chunk(text=text, metadata={"title": "X", "qualified_name": "pkg.mod.X"})
        return SearchResponse(result=ChunkList(items=(item,)), query=query, duration_ms=0.0)

    async def ranked(self, query):
        return ChunkList(items=())


class _FakeApi:
    async def search(self, query):
        return SearchResponse(result=ModuleMemberList(items=()), query=query, duration_ms=0.0)

    async def ranked(self, query):
        return ModuleMemberList(items=())


class _FakeLookup:
    async def lookup(self, payload):
        # Empty target = "list packages"; return a pointer-free listing body.
        return "## Packages\n- pkg"


def _project() -> LoadedProject:
    meta = IndexMetadata(
        project_name="solo",
        project_root="",
        embedding_provider="fastembed",
        embedding_model="bge",
        embedding_dim=384,
        pipeline_hash="h",
        indexed_at=0.0,
    )
    return LoadedProject(name="solo", db_path=Path("/x/solo.db"), metadata=meta)


def _services() -> tuple[ProjectServices, ...]:
    return (
        ProjectServices(
            project=_project(),
            docs=_FakeDocs(),
            api=_FakeApi(),
            lookup=_FakeLookup(),
        ),
    )


def _search_router(surface: str) -> MultiProjectSearch:
    return MultiProjectSearch(
        services=_services(),
        envelope=ResponseEnvelope(
            probe=_StaticProbe(),
            surface=surface,
            pointers_enabled=True,
        ),
    )


def test_mcp_search_response_is_enveloped() -> None:
    out = asyncio.run(_search_router("mcp").search(SearchInput(query="x", kind="docs")))
    assert out.startswith("[index: 8e2110e")
    assert "[[next:" not in out


def test_cli_and_mcp_differ_only_in_pointer_syntax() -> None:
    mcp_out = asyncio.run(_search_router("mcp").search(SearchInput(query="x", kind="docs")))
    cli_out = asyncio.run(_search_router("cli").search(SearchInput(query="x", kind="docs")))

    def normalize(s: str) -> str:
        return s.replace('→ lookup(target="pkg.mod.X")', "<PTR>").replace(
            "→ pydocs-mcp lookup pkg.mod.X", "<PTR>"
        )

    assert normalize(mcp_out) == normalize(cli_out)


def test_router_without_envelope_strips_tokens() -> None:
    # Legacy construction (tests, embedders) must never leak raw tokens.
    router = MultiProjectSearch(services=_services())
    out = asyncio.run(router.search(SearchInput(query="x", kind="docs")))
    assert "[[next:" not in out and not out.startswith("[index:")


def test_lookup_router_enveloped_too() -> None:
    router = MultiProjectLookup(
        services=_services(),
        envelope=ResponseEnvelope(
            probe=_StaticProbe(),
            surface="mcp",
            pointers_enabled=True,
        ),
    )
    out = asyncio.run(router.lookup(LookupInput(target="")))
    assert out.startswith("[index: 8e2110e")
