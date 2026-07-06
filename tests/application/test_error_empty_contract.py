"""Errors and empty results stay enveloped and carry a next step (spec §D1)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.mcp_inputs import LookupInput, SearchInput, SymbolInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
)
from pydocs_mcp.application.null_services import NullDecisionService
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.multirepo import LoadedProject
from pydocs_mcp.storage.index_metadata import IndexMetadata

from ._router_fakes import (
    FakeApi,
    FakeDocs,
    FakeOverview,
    FakeSymbolSource,
    make_envelope,
    make_services,
)


class _RaisingLookup:
    """A lookup that always reports its target missing — drives the
    multi-project ``_lookup_body`` final NotFound raise (the one that must
    carry a search pointer)."""

    async def lookup(self, payload: LookupInput) -> str:
        raise NotFoundError(f"'{payload.target}' not indexed here")


def _project(name: str, indexed_at: float) -> LoadedProject:
    meta = IndexMetadata(
        project_name=name,
        project_root="",
        embedding_provider="fastembed",
        embedding_model="bge",
        embedding_dim=384,
        pipeline_hash="h",
        indexed_at=indexed_at,
    )
    return LoadedProject(name=name, db_path=Path(f"/x/{name}.db"), metadata=meta)


def _raising_services() -> tuple[ProjectServices, ...]:
    # Two loaded projects, both missing the target -> _lookup_body exhausts the
    # recency-ordered loop and hits its final raise.
    return tuple(
        ProjectServices(
            project=_project(name, indexed_at),
            docs=FakeDocs(),
            api=FakeApi(),
            lookup=_RaisingLookup(),
            symbol_source=FakeSymbolSource(),
            overview=FakeOverview(),
            decisions=NullDecisionService(),
        )
        for name, indexed_at in (("a", 1.0), ("b", 2.0))
    )


class _EmptyDocs:
    async def search(self, query):
        from pydocs_mcp.models import ChunkList, SearchResponse

        return SearchResponse(result=ChunkList(items=()), query=query, duration_ms=0.0)

    async def ranked(self, query):
        from pydocs_mcp.models import ChunkList

        return ChunkList(items=())


class _EmptyApi:
    async def search(self, query):
        from pydocs_mcp.models import ModuleMemberList, SearchResponse

        return SearchResponse(result=ModuleMemberList(items=()), query=query, duration_ms=0.0)

    async def ranked(self, query):
        from pydocs_mcp.models import ModuleMemberList

        return ModuleMemberList(items=())


def _empty_search_router() -> ToolRouter:
    """A single-project ToolRouter whose docs/api services return no hits, so
    ``search_codebase`` renders the empty-result body."""
    base = make_services()[0]
    services = (
        ProjectServices(
            project=base.project,
            docs=_EmptyDocs(),
            api=_EmptyApi(),
            lookup=base.lookup,
            symbol_source=base.symbol_source,
            overview=base.overview,
            decisions=base.decisions,
        ),
    )
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


def test_unknown_target_message_carries_search_pointer() -> None:
    # NotFoundError raised by lookup must carry a [[next:search:...]] token so
    # the server-side error text (rendered by the MCP error path) still guides
    # the agent. Drive get_symbol at a missing target across projects that all
    # miss it, and assert the raised NotFoundError's str() carries the token.
    router = ToolRouter(
        services=_raising_services(),
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=_raising_services()),
        lookup_router=MultiProjectLookup(services=_raising_services()),
    )
    with pytest.raises(NotFoundError) as excinfo:
        asyncio.run(router.get_symbol(SymbolInput(target="pkg.mod.Missing")))
    assert "[[next:search:" in str(excinfo.value)


def test_zero_hit_search_points_at_overview() -> None:
    # Fake docs/api services returning empty; assert the enveloped output
    # contains the resolved get_overview() pointer (surface="mcp").
    out = asyncio.run(_empty_search_router().search_codebase(SearchInput(query="nothing here")))
    assert "→ get_overview()" in out
    # And the raw token must not leak through the envelope.
    assert "[[next:" not in out
