"""Search on a named branch, application side (spec §6.4, #312).

Which branch a search pins, how the pin rides on the query, and per-branch
hydration: the rows a pinned search returns carry the selected branch's spans,
and a project row the branch does not hold is never part of its answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydocs_mcp.application.branch_directory import BranchSnapshot
from pydocs_mcp.application.branch_resolution import NULL_RESOLUTION, resolve_branch_selector
from pydocs_mcp.application.branch_search import (
    MembershipChunkHydrator,
    NullChunkBranchHydrator,
    search_branch_pin,
)
from pydocs_mcp.application.docs_search import DocsSearch
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.search_query import (
    build_search_query,
    pinned_to_branch,
    query_for_bundle,
)
from pydocs_mcp.models import (
    NON_GIT_BRANCH_NAME,
    PROJECT_PACKAGE_NAME,
    BranchIndexSource,
    BranchSlice,
    Chunk,
    ChunkList,
    ChunkOrigin,
    SearchQuery,
)
from pydocs_mcp.retrieval.pipeline import PipelineState
from pydocs_mcp.storage.branch_records import BranchRecord, ChunkMembership
from tests._fakes import InMemoryBranchChunkStore, make_fake_uow_factory

MAIN, FEATURE = "main", "feature/x"


def _row(name: str, **kw: object) -> BranchRecord:
    return BranchRecord(name, "a" * 40, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0, **kw)


def _resolve(selector: str, *rows: BranchRecord, live: str | None = MAIN):
    return resolve_branch_selector(selector, BranchSnapshot(rows, rows[0].name, live, {}))


# ── Which branch a search pins ──


def test_a_bundle_holding_one_branch_pins_nothing() -> None:
    """Every project row of a one-branch bundle is that branch's (the project
    GC keeps no row without membership): the query stays exactly as before."""
    resolved = _resolve("", _row(MAIN, is_default=True))
    assert resolved.name == MAIN and resolved.holds_every_project_row
    assert search_branch_pin(resolved) == ""


def test_a_bundle_holding_two_branches_pins_the_resolved_one() -> None:
    rows = (_row(MAIN, is_default=True), _row(FEATURE))
    assert search_branch_pin(_resolve("", *rows)) == MAIN
    assert search_branch_pin(_resolve(FEATURE, *rows)) == FEATURE


def test_the_non_git_placeholder_and_the_null_resolution_pin_nothing() -> None:
    assert search_branch_pin(_resolve("", _row(NON_GIT_BRANCH_NAME, is_default=True))) == ""
    assert search_branch_pin(NULL_RESOLUTION) == ""


# ── The pin rides on the query, beside the request's own filter ──


def test_build_search_query_stamps_only_a_resolved_branch() -> None:
    payload = SearchInput(query="q")
    plain = build_search_query(payload)
    pinned = build_search_query(payload, branch=FEATURE)
    assert plain.branch == "" and pinned.branch == FEATURE
    # The request's filter is the client's, byte for byte (route predicates,
    # schema validation and traces never see the pin).
    assert pinned.pre_filter == plain.pre_filter


def test_pinned_to_branch_is_the_identity_without_a_branch() -> None:
    query = build_search_query(SearchInput(query="q"))
    assert pinned_to_branch(query, "") is query
    assert pinned_to_branch(query, FEATURE).branch == FEATURE


def test_the_dependency_decision_gate_keeps_the_pin() -> None:
    payload = SearchInput(query="q")
    query = build_search_query(payload, branch=FEATURE)
    ran = query_for_bundle(query, payload, holds_dependency_decisions=True)
    assert ran.branch == FEATURE and ran.exclude_dependency_decisions


# ── Per-branch hydration ──


def _project(chunk_id: int, **spans: object) -> Chunk:
    return Chunk(
        text=f"c{chunk_id}", id=chunk_id, metadata={"package": PROJECT_PACKAGE_NAME, **spans}
    )


def _hydrator(*rows: ChunkMembership) -> MembershipChunkHydrator:
    store = InMemoryBranchChunkStore()
    for row in rows:
        store.rows.setdefault(row.branch, []).append(row)
    return MembershipChunkHydrator(uow_factory=make_fake_uow_factory(branch_chunks=store))


async def test_hydration_takes_the_selected_branch_spans_and_drops_rows_it_does_not_hold() -> None:
    shared = _project(1, source_path="app/a.py", start_line=1, end_line=2)
    main_only = _project(2, source_path="app/a.py", start_line=4, end_line=5)
    dependency = Chunk(text="d", id=3, metadata={"package": "requests", "start_line": 9})
    composite = Chunk(text="body", metadata={"origin": ChunkOrigin.COMPOSITE_OUTPUT.value})
    hydrator = _hydrator(
        ChunkMembership(FEATURE, 1, "app/a.py", 11, 12),
        ChunkMembership(MAIN, 2, "app/a.py", 4, 5),
    )
    out = await hydrator.hydrate_on_branch((shared, main_only, dependency, composite), FEATURE)
    assert out == (
        _project(1, source_path="app/a.py", start_line=11, end_line=12),
        dependency,
        composite,
    )


async def test_hydration_reads_only_the_tree_slice_and_keeps_scores() -> None:
    scored = Chunk(
        text="c1",
        id=1,
        relevance=0.5,
        retriever_name="dense",
        metadata={"package": PROJECT_PACKAGE_NAME},
    )
    hydrator = _hydrator(ChunkMembership(FEATURE, 1, "", None, None, slice=BranchSlice.DIFF))
    assert await hydrator.hydrate_on_branch((scored,), FEATURE) == ()
    kept = await _hydrator(ChunkMembership(FEATURE, 1, "")).hydrate_on_branch((scored,), FEATURE)
    # A membership row without a span leaves the row span-less, like row_to_chunk.
    assert kept == (scored,) and kept[0].relevance == 0.5


async def test_the_null_hydrator_returns_the_rows_untouched() -> None:
    rows = (_project(1),)
    assert await NullChunkBranchHydrator().hydrate_on_branch(rows, FEATURE) is rows


@dataclass
class _Pipeline:
    state_items: tuple[Chunk, ...]
    queries: list[SearchQuery] = field(default_factory=list)

    async def run(self, query: SearchQuery) -> PipelineState:
        self.queries.append(query)
        items = ChunkList(items=self.state_items)
        return PipelineState(query=query, result=items, candidates=items, duration_ms=0.0)


@dataclass
class _SpyHydrator:
    calls: list[str] = field(default_factory=list)

    async def hydrate_on_branch(self, chunks, branch):
        self.calls.append(branch)
        return tuple(c for c in chunks if c.id != 2)


def _query(branch: str = "") -> SearchQuery:
    return SearchQuery(terms="q", pre_filter={"scope": "all"}, branch=branch)


async def test_docs_search_hydrates_its_rows_on_the_pinned_branch_only() -> None:
    spy = _SpyHydrator()
    docs = DocsSearch(chunk_pipeline=_Pipeline((_project(1), _project(2))), branch_hydrator=spy)
    unpinned = await docs.search(_query())
    assert spy.calls == [] and [c.id for c in unpinned.candidates.items] == [1, 2]
    pinned = await docs.search(_query(FEATURE))
    assert set(spy.calls) == {FEATURE}
    assert [c.id for c in pinned.candidates.items] == [1]
    assert [c.id for c in pinned.result.items] == [1]


@dataclass
class _RankedPresetPipeline:
    """A preset without ``token_budget_formatter``: ``state.result`` stays None
    and the response serves the ranked candidates as its result."""

    state_items: tuple[Chunk, ...]

    async def run(self, query: SearchQuery) -> PipelineState:
        items = ChunkList(items=self.state_items)
        return PipelineState(query=query, result=None, candidates=items, duration_ms=0.0)


async def test_a_ranked_preset_hydrates_its_rows_once() -> None:
    spy = _SpyHydrator()
    pipeline = _RankedPresetPipeline((_project(1), _project(2)))
    docs = DocsSearch(chunk_pipeline=pipeline, branch_hydrator=spy)
    response = await docs.search(_query(FEATURE))
    assert spy.calls == [FEATURE]
    assert response.result is response.candidates
    assert [c.id for c in response.result.items] == [1]


async def test_docs_ranked_hydrates_on_the_pinned_branch_only() -> None:
    spy = _SpyHydrator()
    docs = DocsSearch(chunk_pipeline=_Pipeline((_project(1), _project(2))), branch_hydrator=spy)
    assert [c.id for c in (await docs.ranked(_query())).items] == [1, 2] and spy.calls == []
    assert [c.id for c in (await docs.ranked(_query(FEATURE))).items] == [1]
    assert spy.calls == [FEATURE]
