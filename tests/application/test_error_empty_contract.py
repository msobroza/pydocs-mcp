"""Errors and empty results stay enveloped and carry a next step (spec §D1)."""

from __future__ import annotations

import asyncio
import json
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
from pydocs_mcp.application.suggestions import SEARCH_ZERO_HIT_SUGGESTION
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.retrieval.config import SuggestionsConfig
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

    async def lookup_with_items(self, payload: LookupInput) -> str:
        return await self.lookup(payload)  # always raises before returning


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


def _empty_search_router(suggestions: SuggestionsConfig | None = None) -> ToolRouter:
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
        suggestions=suggestions or SuggestionsConfig(),
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
    out = asyncio.run(
        _empty_search_router().search_codebase(SearchInput(query="nothing here"))
    ).text
    assert "→ get_overview()" in out
    # And the raw token must not leak through the envelope.
    assert "[[next:" not in out


def test_zero_hit_search_meta_carries_suggestion() -> None:
    # ADR 0007: the fired rule is machine-readable as meta.suggestion so
    # transcript analysis can attribute the nudge to machinery (R7).
    resp = asyncio.run(_empty_search_router().search_codebase(SearchInput(query="nothing here")))
    assert resp.meta["suggestion"] == SEARCH_ZERO_HIT_SUGGESTION


def test_zero_hit_search_flag_off_strips_pointer_and_suggestion() -> None:
    # search_zero_hit off ⇒ the bare empty-result body (the pre-pointer
    # bytes) and no suggestion key anywhere in meta.
    on = asyncio.run(_empty_search_router().search_codebase(SearchInput(query="nothing here")))
    off = asyncio.run(
        _empty_search_router(SuggestionsConfig(search_zero_hit=False)).search_codebase(
            SearchInput(query="nothing here")
        )
    )
    assert "get_overview" not in off.text
    assert "suggestion" not in off.meta
    # The default-on response differs from flag-off ONLY by the pointer line
    # (search_zero_hit is behavior-preserving by construction, ADR 0007).
    assert on.text.replace("\n→ get_overview()", "") == off.text


def test_zero_hit_search_fired_rule_emits_structured_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO", logger="pydocs_mcp.application.suggestions"):
        asyncio.run(_empty_search_router().search_codebase(SearchInput(query="nothing here")))
    events = [json.loads(r.message) for r in caplog.records]
    assert {
        "event": "suggestion_fired",
        "tool": "search_codebase",
        "rule": "search_zero_hit",
    } in events
