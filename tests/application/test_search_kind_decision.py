"""kind="decision" routing: input literal, origin pre-filter arm, YAML route,
and the ToolRouter end-to-end decision-record render (Task 4)."""

from __future__ import annotations

import asyncio

from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
)
from pydocs_mcp.application.search_query import build_search_query
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.models import (
    ChunkFilterField,
    ChunkOrigin,
    SearchQuery,
)
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.pipeline import PipelineState
from pydocs_mcp.retrieval.route_predicates import default_predicate_registry

from ._router_fakes import make_envelope, make_project


# ── SearchInput.kind gains "decision" ──


def test_search_input_accepts_kind_decision() -> None:
    payload = SearchInput(query="why sqlite", kind="decision")
    assert payload.kind == "decision"


# ── build_search_query origin pre-filter arm ──


def test_build_search_query_adds_origin_for_decision() -> None:
    query = build_search_query(SearchInput(query="why sqlite", kind="decision"))
    assert query.pre_filter[ChunkFilterField.ORIGIN.value] == ChunkOrigin.DECISION_RECORD.value


def test_build_search_query_omits_origin_for_non_decision() -> None:
    for kind in ("docs", "api", "any"):
        query = build_search_query(SearchInput(query="x", kind=kind))
        assert ChunkFilterField.ORIGIN.value not in (query.pre_filter or {})


# ── YAML route predicate kind_is_decision ──


def _state(origin: str | None) -> PipelineState:
    pre = {ChunkFilterField.ORIGIN.value: origin} if origin else {}
    return PipelineState(query=SearchQuery(terms="x", pre_filter=pre))


def test_kind_is_decision_truth_table() -> None:
    pred = default_predicate_registry.get("kind_is_decision")
    assert pred(_state(ChunkOrigin.DECISION_RECORD.value))
    assert not pred(_state(ChunkOrigin.PROJECT_CODE_SECTION.value))
    assert not pred(_state(None))


def test_default_config_routes_decision_to_decision_preset() -> None:
    cfg = AppConfig.load()
    routes = cfg.pipelines["chunk"].routes
    decision_routes = [r for r in routes if r.predicate == "kind_is_decision"]
    assert len(decision_routes) == 1
    assert decision_routes[0].pipeline_path.name == "decision_search.yaml"
    # Default still resolves to the graph preset.
    assert routes[-1].default and routes[-1].pipeline_path.name == "chunk_search_graph.yaml"


# ── ToolRouter end-to-end: decision query renders record blocks ──


class _FakeDecisions:
    """A DecisionNavigator whose ``search`` renders a decision-record block —
    proving the router delegates to it instead of the raw-chunk render path."""

    async def search(self, query: str) -> str:
        return "## Decision — Use SQLite\n\nrationale body"

    async def for_targets(self, targets: list[str], *, query: str = "") -> str:
        raise AssertionError("kind=decision search must not hit for_targets")

    async def dashboard(self) -> str:
        raise AssertionError("kind=decision search must not hit dashboard")


class _FakeDocs:
    async def search(self, query):
        from pydocs_mcp.models import Chunk, ChunkList, SearchResponse

        item = Chunk(text="RAW_CHUNK", metadata={"title": "c"})
        return SearchResponse(result=ChunkList(items=(item,)), query=query, duration_ms=0.0)

    async def ranked(self, query):
        from pydocs_mcp.models import ChunkList

        return ChunkList(items=())


class _FakeApi:
    async def search(self, query):
        from pydocs_mcp.models import ModuleMemberList, SearchResponse

        return SearchResponse(result=ModuleMemberList(items=()), query=query, duration_ms=0.0)

    async def ranked(self, query):
        from pydocs_mcp.models import ModuleMemberList

        return ModuleMemberList(items=())


def _services() -> tuple[ProjectServices, ...]:
    return (
        ProjectServices(
            project=make_project(),
            docs=_FakeDocs(),
            api=_FakeApi(),
            lookup=None,  # unused by kind=decision search
            symbol_source=None,
            overview=None,
            decisions=_FakeDecisions(),
        ),
    )


def _router() -> ToolRouter:
    services = _services()
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


def test_search_codebase_kind_decision_renders_record_block() -> None:
    out = asyncio.run(_router().search_codebase(SearchInput(query="why sqlite", kind="decision")))
    assert "## Decision — Use SQLite" in out
    assert "RAW_CHUNK" not in out
