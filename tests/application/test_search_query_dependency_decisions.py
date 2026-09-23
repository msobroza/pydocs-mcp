"""Ordinary searches leave a bundle's dependency decisions out (#346).

With ``decision_capture.include_deps`` on at INDEX time, a dependency's mined
decisions are also ordinary chunks of that dependency. Whether a search has to
leave them out is a fact about the bundle it runs over, not about the config of
the process answering it: a bundle is often indexed under one config and served
read-only under another (``--workspace`` / ``--db``, the GPU-index / CPU-serve
split). So the composition root reads it once per loaded bundle
(``ProjectServices.holds_dependency_decisions``, one ``EXISTS`` through the
decision store), and :func:`query_for_bundle` asks for the exclusion only over
such a bundle, and only when the request does not ask for a dependency itself
(``kind="decision"`` and a ``package=`` naming one do). A stock bundle runs
exactly the query ``build_search_query`` always built, so its filter and plan
cannot move.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_mcp.application.decision_corpus import bundle_holds_dependency_decisions
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.multi_project_search import MultiProjectSearch, ProjectServices
from pydocs_mcp.application.search_query import build_search_query, query_for_bundle
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    ChunkList,
    SearchQuery,
    SearchResponse,
)
from pydocs_mcp.multirepo import load_project
from pydocs_mcp.retrieval.config import AppConfig, DecisionCaptureConfig
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.pre_filter import PreFilterStep
from pydocs_mcp.server import _build_project_services
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from tests._fakes import InMemoryDecisionStore, make_fake_uow_factory

from ._decision_fakes import decision_record
from ._router_fakes import make_service

_ORDINARY_PAYLOADS = (
    SearchInput(query="x"),
    SearchInput(query="x", kind="docs"),
    SearchInput(query="x", kind="api"),
    SearchInput(query="x", scope="deps"),
    SearchInput(query="x", scope="project"),
    SearchInput(query="x", package=PROJECT_PACKAGE_NAME),
)


@pytest.mark.parametrize("payload", _ORDINARY_PAYLOADS, ids=repr)
def test_build_search_query_builds_the_query_it_always_built(payload: SearchInput) -> None:
    """Terms, cap and pre-filter — nothing else departs from the defaults."""
    query = build_search_query(payload)
    assert query.exclude_dependency_decisions is False
    assert query == SearchQuery(
        terms=query.terms, max_results=query.max_results, pre_filter=query.pre_filter
    )


@pytest.mark.parametrize("payload", _ORDINARY_PAYLOADS, ids=repr)
def test_a_stock_bundle_runs_the_query_it_always_ran(payload: SearchInput) -> None:
    query = build_search_query(payload)
    assert query_for_bundle(query, payload, holds_dependency_decisions=False) is query


@pytest.mark.parametrize("payload", _ORDINARY_PAYLOADS, ids=repr)
def test_a_bundle_with_dependency_decisions_leaves_them_out_of_ordinary_searches(
    payload: SearchInput,
) -> None:
    """``scope="deps"`` alone does not count as asking; nothing but the flag
    moves (the exclusion rides beside the pre-filter, not in the user syntax)."""
    query = build_search_query(payload)
    ran = query_for_bundle(query, payload, holds_dependency_decisions=True)
    assert ran.exclude_dependency_decisions is True
    assert replace(ran, exclude_dependency_decisions=False) == query


@pytest.mark.parametrize("package", ["flask_login", "Flask-Login"])
def test_a_package_naming_a_dependency_keeps_its_decisions(package: str) -> None:
    for kind in ("any", "docs"):
        payload = SearchInput(query="x", kind=kind, package=package)
        query = build_search_query(payload)
        assert query_for_bundle(query, payload, holds_dependency_decisions=True) is query, kind


def test_kind_decision_keeps_its_own_corpus_rule() -> None:
    """kind="decision" keeps its own corpus rule (decision_corpus.py)."""
    payload = SearchInput(query="x", kind="decision")
    query = build_search_query(payload)
    assert query_for_bundle(query, payload, holds_dependency_decisions=True) is query


def test_the_exclusion_needs_a_pre_filter_to_ride_on() -> None:
    """The fetchers read the pre-filter's tree only when a pre_filter is set, so
    an exclusion without one would silently do nothing — refuse it."""
    with pytest.raises(ValueError, match="exclude_dependency_decisions"):
        SearchQuery(terms="x", exclude_dependency_decisions=True)


# ── The bundle gate: one EXISTS through the decision store ───────────────────


async def test_the_bundle_gate_counts_only_a_package_other_than_the_project() -> None:
    decisions = InMemoryDecisionStore()
    uow_factory = make_fake_uow_factory(decisions=decisions)
    assert await bundle_holds_dependency_decisions(uow_factory) is False
    await decisions.upsert((decision_record(id=1, title="ours"),))
    assert await bundle_holds_dependency_decisions(uow_factory) is False
    await decisions.upsert((decision_record(id=2, title="theirs", package="requests"),))
    assert await bundle_holds_dependency_decisions(uow_factory) is True
    assert [call.method for call in decisions.calls].count("has_dependency_records") == 3


async def _seed_decision(db: Path, package: str) -> None:
    record = replace(decision_record(id=1, title=f"{package} reason", package=package), id=None)
    async with build_sqlite_uow_factory(db)() as uow:
        await uow.decisions.upsert((record,))
        await uow.commit()


@pytest.mark.parametrize(
    "config",
    [
        AppConfig(),
        AppConfig(decision_capture=DecisionCaptureConfig(include_deps=True)),
        AppConfig(decision_capture=DecisionCaptureConfig(enabled=False)),
    ],
    ids=["stock", "include_deps", "capture_disabled"],
)
@pytest.mark.parametrize(
    ("seeded", "holds"),
    [((), False), ((PROJECT_PACKAGE_NAME,), False), ((PROJECT_PACKAGE_NAME, "requests"), True)],
    ids=["empty", "project_only", "with_a_dependency"],
)
def test_the_composition_root_reads_the_bundle_not_the_serving_config(
    tmp_path: Path, config: AppConfig, seeded: tuple[str, ...], holds: bool
) -> None:
    """Whatever the answering process's ``decision_capture`` says, the gate is
    what the loaded bundle holds."""
    db = tmp_path / "bundle.db"
    open_index_database(db).close()
    for package in seeded:
        asyncio.run(_seed_decision(db, package))
    services = _build_project_services(load_project(db), config)
    assert services.holds_dependency_decisions is holds


# ── The router: per bundle, on the single-project and the union path ────────


class RecordingDocs:
    """DocsSearch stand-in that records every query it is asked to run."""

    def __init__(self) -> None:
        self.queries: list[SearchQuery] = []

    async def search(self, query: SearchQuery) -> SearchResponse:
        self.queries.append(query)
        return SearchResponse(result=ChunkList(items=()), query=query, duration_ms=0.0)

    async def ranked(self, query: SearchQuery) -> ChunkList:
        self.queries.append(query)
        return ChunkList(items=())


def _recording_service(name: str, *, holds: bool) -> ProjectServices:
    return replace(make_service(name), docs=RecordingDocs(), holds_dependency_decisions=holds)


@pytest.mark.parametrize("holds", [False, True])
@pytest.mark.parametrize("kind", ["any", "docs"])
def test_a_single_bundle_search_follows_that_bundle(holds: bool, kind: str) -> None:
    svc = _recording_service("solo", holds=holds)
    asyncio.run(MultiProjectSearch(services=(svc,))._search_body(SearchInput(query="x", kind=kind)))
    assert svc.docs.queries
    assert all(query.exclude_dependency_decisions is holds for query in svc.docs.queries)


@pytest.mark.parametrize("kind", ["any", "docs"])
def test_the_union_runs_each_bundle_under_its_own_gate(kind: str) -> None:
    """A union of a mined and a stock bundle: only the mined one filters."""
    mined = _recording_service("mined", holds=True)
    stock = _recording_service("stock", holds=False)
    payload = SearchInput(query="x", kind=kind)
    asyncio.run(MultiProjectSearch(services=(mined, stock))._search_body(payload))
    assert mined.docs.queries and stock.docs.queries
    assert all(query.exclude_dependency_decisions for query in mined.docs.queries)
    assert stock.docs.queries == [build_search_query(payload)] * len(stock.docs.queries)


async def test_a_stock_bundle_s_filter_tree_is_the_one_it_always_was() -> None:
    """The query a stock bundle runs publishes the same pre-filter tree — none,
    for the scope-only default — so the dense branch keeps its ANN path."""
    stock = _recording_service("stock", holds=False)
    payload = SearchInput(query="x")
    await MultiProjectSearch(services=(stock,))._search_body(payload)
    step = PreFilterStep(
        allowed_fields=frozenset({"package", "module", "scope", "origin"}),
        schema_name="chunk",
        target_field="chunk",
    )
    ran = await step.run(RetrieverState(query=stock.docs.queries[0]))
    stock_main = await step.run(RetrieverState(query=build_search_query(payload)))
    assert ran.scratch["pre_filter.result"] == stock_main.scratch["pre_filter.result"]
    assert ran.scratch["pre_filter.result"].tree is None
