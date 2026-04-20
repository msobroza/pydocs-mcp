"""Tests for SearchApiService — thin wrapper around member_pipeline.run (spec §5.1).

These tests use a minimal fake pipeline that structurally mimics
CodeRetrieverPipeline's .run() coroutine. No SQLite, no real stages — the
service is a pure dispatch layer so the tests exercise dispatch only.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from pydocs_mcp.application.search_api_service import SearchApiService
from pydocs_mcp.models import (
    MemberKind,
    ModuleMember,
    ModuleMemberList,
    SearchQuery,
    SearchResponse,
)
from pydocs_mcp.retrieval.pipeline import PipelineState


# ── Fake pipeline ─────────────────────────────────────────────────────────


@dataclass
class FakeMemberPipeline:
    """Structurally satisfies CodeRetrieverPipeline.run — returns canned state."""

    state: PipelineState
    calls: list[SearchQuery] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.calls = []

    async def run(self, query: SearchQuery) -> PipelineState:
        self.calls.append(query)
        return self.state


@dataclass
class RaisingPipeline:
    """Propagates a given exception from run()."""

    exc: Exception

    async def run(self, query: SearchQuery) -> PipelineState:  # noqa: ARG002
        raise self.exc


# ── Tests ─────────────────────────────────────────────────────────────────


def _member(name: str) -> ModuleMember:
    return ModuleMember(
        id=1,
        relevance=0.8,
        metadata={
            "name": name,
            "module": "pkg.mod",
            "package": "pkg",
            "kind": MemberKind.FUNCTION.value,
        },
    )


@pytest.mark.asyncio
async def test_search_returns_response_with_module_member_list() -> None:
    query = SearchQuery(terms="predict", max_results=5)
    members = ModuleMemberList(items=(_member("predict"), _member("predict_batch")))
    state = PipelineState(query=query, result=members, duration_ms=3.3)
    service = SearchApiService(member_pipeline=FakeMemberPipeline(state=state))

    response = await service.search(query)

    assert isinstance(response, SearchResponse)
    assert response.result is members
    assert response.query is query
    assert response.duration_ms == pytest.approx(3.3)


@pytest.mark.asyncio
async def test_search_empty_result_defaults_to_empty_member_list() -> None:
    query = SearchQuery(terms="no match")
    state = PipelineState(query=query, result=None, duration_ms=0.5)
    service = SearchApiService(member_pipeline=FakeMemberPipeline(state=state))

    response = await service.search(query)

    assert isinstance(response.result, ModuleMemberList)
    assert response.result.items == ()
    assert response.query is query


@pytest.mark.asyncio
async def test_search_threads_duration_ms_from_state() -> None:
    query = SearchQuery(terms="timing")
    members = ModuleMemberList(items=(_member("f"),))
    state = PipelineState(query=query, result=members, duration_ms=12.5)
    service = SearchApiService(member_pipeline=FakeMemberPipeline(state=state))

    response = await service.search(query)

    assert response.duration_ms == pytest.approx(12.5)


@pytest.mark.asyncio
async def test_search_propagates_pipeline_exception() -> None:
    query = SearchQuery(terms="boom")
    service = SearchApiService(
        member_pipeline=RaisingPipeline(exc=RuntimeError("pipeline failed"))
    )

    with pytest.raises(RuntimeError, match="pipeline failed"):
        await service.search(query)


def test_service_is_frozen_slotted_dataclass() -> None:
    service = SearchApiService(
        member_pipeline=FakeMemberPipeline(
            state=PipelineState(query=SearchQuery(terms="x"))
        )
    )
    with pytest.raises((AttributeError, Exception)):
        service.member_pipeline = None  # type: ignore[misc]
    assert not hasattr(service, "__dict__")
