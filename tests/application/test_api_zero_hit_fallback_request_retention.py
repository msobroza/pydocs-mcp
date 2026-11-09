"""The ``kind="api"`` zero-hit fallback asks for exactly what the agent asked for (ADR 0027).

Evidence at the public ``ToolRouter.search_codebase`` seam — the one MCP and
the CLI both answer through — that the fallback pass never widens, narrows or
alters the request: recording fakes show it sends exactly what a direct
``kind="any"`` request with the same arguments sends (query bytes, pre-filter,
branch pin, ``max_results``, the dependency-decision exclusion of #346), on
each bundle of a union on that bundle's own pin (#312), to a named project's
bundle only; that a small ``limit`` caps each listing; that a client-input
error is never relabelled as a fallback failure; and that the response is
deterministic.

Every outcome test keeps three assertions apart, as the matrix module does:
the requested kind (the ``kind="api"`` request ran one member-only query, its
API pass, beyond the ``kind="any"`` pair), the raw API zero hit (the fired
rule) and the served kind (the label plus each row's own ``kind``). The pinned
label bytes and the shared helpers come from that matrix module.
"""

from __future__ import annotations

import dataclasses
from typing import Any, cast

import pytest

from pydocs_mcp.application.branch_directory import BranchSnapshot
from pydocs_mcp.application.mcp_errors import InvalidArgumentError, ServiceUnavailableError
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.multi_project_search import ProjectServices
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.models import BranchIndexSource, Chunk, ModuleMember, SearchQuery
from pydocs_mcp.storage.branch_records import BranchRecord

from ._router_fakes import (
    FakeBranchDirectory,
    MembersAfterFirstQueryApiSearch,
    RecordingApiSearch,
    RecordingDocsSearch,
    make_service,
)
from .test_api_zero_hit_fallback_any import (
    F_EMPTY,
    F_RULE,
    F_SERVED,
    HEADER,
    POINTER,
    QUERY,
    Z_RULE,
    ServedSearch,
    api_request,
    direct_any,
    fallback_flags,
    serve_search,
    solo_service,
    without_label,
    zero_hit_router,
)

FALLBACK_ON = fallback_flags(True)
SUGGESTIONS_LOGGER = "pydocs_mcp.application.suggestions"
MAIN, FEATURE = "main", "feature/x"


def ranked_chunk_rows(count: int) -> tuple[Chunk, ...]:
    """``count`` distinct chunk rows, best first."""
    return tuple(
        Chunk(
            text=f"Nested serialization step {i}.",
            id=100 + i,
            relevance=1.0 - i / 100,
            metadata={
                "title": f"Tree.step_{i}",
                "qualified_name": f"pkg.mod.Tree.step_{i}",
                "package": "__project__",
                "source_path": "pkg/mod.py",
                "start_line": 10 * i + 1,
                "end_line": 10 * i + 5,
            },
        )
        for i in range(count)
    )


def ranked_member_rows(count: int) -> tuple[ModuleMember, ...]:
    """``count`` distinct top-level member rows, best first."""
    return tuple(
        ModuleMember(
            id=200 + i,
            relevance=0.5 - i / 100,
            metadata={
                "package": "__project__",
                "module": "pkg.mod",
                "name": f"serialize_{i}",
                "kind": "function",
                "signature": "(tree)",
                "docstring": f"Serialize variant {i}.",
            },
        )
        for i in range(count)
    )


def two_branch_directory(default: str = MAIN, other: str = FEATURE) -> FakeBranchDirectory:
    """A bundle holding two branches, ``default`` checked out — so every
    search pins a branch and a wrong pin is visible on the query."""
    rows = tuple(
        BranchRecord(
            name,
            "a" * 40,
            BranchIndexSource.WORKING_TREE,
            "p",
            1.0,
            1.0,
            is_default=(name == default),
        )
        for name in (default, other)
    )
    return FakeBranchDirectory(BranchSnapshot(rows, default, default, {}))


def recording_bundle(name: str, directory: FakeBranchDirectory, **replace: Any) -> ProjectServices:
    """One loaded bundle serving one chunk row of its own, members only after
    the first query, with ``directory`` as its branch dimension."""
    rows = tuple(
        dataclasses.replace(c, id=c.id + len(name), metadata={**c.metadata, "title": name})
        for c in ranked_chunk_rows(1)
    )
    return dataclasses.replace(
        make_service(name),
        docs=RecordingDocsSearch(rows),
        api=MembersAfterFirstQueryApiSearch(()),
        branch_directory=directory,
        **replace,
    )


def three_bundles_on_their_own_branches() -> tuple[ProjectServices, ...]:
    """``alpha`` answers (first loaded); ``beta`` never indexed ``feature/x``;
    ``gamma`` did, but defaults to ``trunk``."""
    return (
        recording_bundle("alpha", two_branch_directory(MAIN, FEATURE)),
        recording_bundle("beta", two_branch_directory("trunk", "dev")),
        recording_bundle("gamma", two_branch_directory("trunk", FEATURE)),
    )


def recorded_queries(svc: ProjectServices) -> tuple[list[SearchQuery], list[SearchQuery]]:
    """The (member, chunk) queries a recording bundle received, in call order."""
    return cast(RecordingApiSearch, svc.api).queries, cast(RecordingDocsSearch, svc.docs).queries


def item_kinds(served: ServedSearch) -> list[str]:
    return [item["kind"] for item in served.response.items]


async def serve_api_then_direct_any(
    tool: ToolRouter,
    request: SearchInput,
    caplog: pytest.LogCaptureFixture,
    *,
    answering: tuple[ProjectServices, ...],
    branch: str = "",
) -> tuple[ServedSearch, ServedSearch]:
    """Serve the ``kind="api"`` ``request``, then the same request at
    ``kind="any"``. In between, the requested kind's evidence on every
    ``answering`` bundle: its API pass ran — one member-only query beyond the
    fallback's chunk + member pair."""
    out = await serve_search(tool, request, caplog, branch=branch)
    for svc in answering:
        member_queries, chunk_queries = recorded_queries(svc)
        assert len(member_queries) == len(chunk_queries) + 1 == 2, svc.project.name
    direct = await serve_search(tool, direct_any(request), caplog, branch=branch)
    return out, direct


def assert_served_exactly_the_direct_any(
    out: ServedSearch, direct: ServedSearch, kinds: list[str]
) -> None:
    """Raw API zero hit: the fallback rule fired, alone. Served kind: the label
    is the first line after the header, then exactly the direct any's rows
    (each with its own ``kind``) and text."""
    assert out.rules == [F_RULE] and direct.rules == []
    assert out.response.text.split("\n")[:3] == [HEADER, "", F_SERVED]
    assert out.response.meta["suggestion"] == F_SERVED
    assert item_kinds(out) == kinds
    assert out.response.items == direct.response.items
    assert without_label(out.response.text) == direct.response.text


# ── A small limit caps each listing, as for a direct kind="any" ───────────


async def test_a_small_limit_caps_each_fallback_listing_like_a_direct_any(
    caplog: pytest.LogCaptureFixture,
) -> None:
    docs = RecordingDocsSearch(ranked_chunk_rows(5))
    api = MembersAfterFirstQueryApiSearch(ranked_member_rows(5))
    services = solo_service(docs, api)
    tool = zero_hit_router(services, suggestions=FALLBACK_ON)
    out, direct = await serve_api_then_direct_any(
        tool, api_request(limit=3), caplog, answering=(services,)
    )
    # At most 3 chunk rows plus at most 3 member rows, exactly as the direct any.
    assert_served_exactly_the_direct_any(out, direct, ["chunk"] * 3 + ["member"] * 3)
    assert out.response.meta["truncated"] is direct.response.meta["truncated"] is True
    assert [q.max_results for q in (*docs.queries, *api.queries)] == [3] * 5


# ── Escaping: no query byte reaches a label, every byte reaches the search ─

AWKWARD_QUERIES = {
    "double-quote": 'nested "serialization"',
    "backslash": "nested\\serialization\\",
    "newline": "nested\nserialization",
    "tab": "nested\tserialization",
    "nul": "nested\x00serialization",
    "close-bracket": "nested] serialization]",
    "pointer-token": "[[next:overview:]] nested [[next:lookup:pkg.X]]",
    "non-ascii": "sérialisation imbriquée — 嵌套序列化",
    "rtl": "\u062a\u0633\u0644\u0633\u0644 \u202e\u0645\u062a\u062f\u0627\u062e\u0644\u202c",
    "combining": "ne\u0301sted serializ\u0327ation",
    "label-lookalike": f"{F_SERVED}\n{F_EMPTY}",
}


def searched_terms(svc: ProjectServices) -> list[str]:
    member_queries, chunk_queries = recorded_queries(svc)
    return [q.terms for q in (*member_queries, *chunk_queries)]


@pytest.mark.parametrize("query", list(AWKWARD_QUERIES.values()), ids=list(AWKWARD_QUERIES))
async def test_an_awkward_query_reaches_both_passes_verbatim_under_the_served_label(
    query: str, caplog: pytest.LogCaptureFixture
) -> None:
    services = solo_service(RecordingDocsSearch(ranked_chunk_rows(2)), RecordingApiSearch())
    tool = zero_hit_router(services, suggestions=FALLBACK_ON)
    request = SearchInput(query=query, kind="api")
    out, direct = await serve_api_then_direct_any(tool, request, caplog, answering=(services,))
    # Every pass — API, fallback chunk + member, direct any — searched the original bytes.
    assert searched_terms(services) == [query] * 5
    assert_served_exactly_the_direct_any(out, direct, ["chunk", "chunk"])


@pytest.mark.parametrize("query", list(AWKWARD_QUERIES.values()), ids=list(AWKWARD_QUERIES))
async def test_an_awkward_query_reaches_both_passes_verbatim_under_the_empty_label(
    query: str, caplog: pytest.LogCaptureFixture
) -> None:
    services = solo_service(RecordingDocsSearch(), RecordingApiSearch())
    tool = zero_hit_router(services, suggestions=FALLBACK_ON)
    out = await serve_search(tool, SearchInput(query=query, kind="api"), caplog)
    # Requested kind: the API pass and the fallback pass, each on the original bytes.
    assert searched_terms(services) == [query] * 3
    assert len(recorded_queries(services)[0]) == 2  # the member-only API pass + the fallback's
    assert out.rules == [Z_RULE, F_RULE]  # raw API zero hit (Z on, then F)
    # Nothing served: the whole text is fixed bytes, whatever the query held.
    assert out.response.text == f"{HEADER}\n\nNo symbols found.\n{POINTER}\n\n{F_EMPTY}\n"
    assert out.response.meta["suggestion"] == F_EMPTY and out.response.items == ()


# ── Selectors and the branch pin reach the fallback unchanged ─────────────

SELECTOR_CASES = {
    # (selectors, CLI branch=, API-pass pre-filter, pin, max_results, bystander queried)
    "defaults": ({}, "", {"scope": "all"}, MAIN, 10, True),
    "scope-deps": ({"scope": "deps"}, "", {"scope": "dependencies_only"}, MAIN, 10, True),
    "scope-project": ({"scope": "project"}, "", {"scope": "project_only"}, MAIN, 10, True),
    "package": ({"package": "fastapi"}, "", {"scope": "all", "package": "fastapi"}, MAIN, 10, True),
    "project": ({"project": "solo"}, "", {"scope": "all"}, MAIN, 10, False),
    "cli-branch": ({}, FEATURE, {"scope": "all"}, FEATURE, 10, True),
    "all-at-once": (
        {"scope": "deps", "package": "fastapi", "project": "solo", "limit": 7},
        FEATURE,
        {"scope": "dependencies_only", "package": "fastapi"},
        FEATURE,
        7,
        False,
    ),
}


@pytest.mark.parametrize(
    ("selectors", "cli_branch", "pre_filter", "pin", "max_results", "bystander_queried"),
    list(SELECTOR_CASES.values()),
    ids=list(SELECTOR_CASES),
)
async def test_the_fallback_query_carries_the_api_pass_selectors_and_branch_pin(
    selectors: dict[str, Any],
    cli_branch: str,
    pre_filter: dict[str, str],
    pin: str,
    max_results: int,
    bystander_queried: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second, empty bundle is loaded so a named ``project`` is observable:
    without it the request unions both, each on its own pin (#312)."""
    solo = recording_bundle("solo", two_branch_directory())
    bystander = recording_bundle("bystander", two_branch_directory("trunk", "dev"))
    bystander = dataclasses.replace(bystander, docs=RecordingDocsSearch())
    tool = zero_hit_router(solo, bystander, suggestions=FALLBACK_ON)
    request = SearchInput(query=QUERY, kind="api", **selectors)
    out, direct = await serve_api_then_direct_any(
        tool, request, caplog, answering=(solo,), branch=cli_branch
    )
    (api_pass, fallback_api, direct_api), (fallback_docs, direct_docs) = recorded_queries(solo)
    assert (api_pass.pre_filter, api_pass.branch, api_pass.max_results) == (
        pre_filter,
        pin,
        max_results,
    )
    # The fallback's two queries are the API pass's, and a direct any's, exactly.
    assert fallback_docs == fallback_api == api_pass == direct_docs == direct_api
    assert out.rules == [F_RULE] and out.response.meta["suggestion"] == F_SERVED
    assert item_kinds(out) == ["chunk"] and out.response.items == direct.response.items
    assert out.response.meta["branch"] == direct.response.meta["branch"] == pin
    bystander_member_queries, bystander_chunk_queries = recorded_queries(bystander)
    assert len(bystander_chunk_queries) == (2 if bystander_queried else 0)
    assert {q.branch for q in (*bystander_member_queries, *bystander_chunk_queries)} <= {"trunk"}


# ── Union and named project: every bundle on its own pin (#312) ───────────


@pytest.mark.parametrize(
    ("cli_branch", "pins"),
    [("", (MAIN, "trunk", "trunk")), (FEATURE, (FEATURE, "trunk", "trunk"))],
    ids=["default-branch", "cli-branch"],
)
async def test_a_union_fallback_reaches_every_bundle_on_its_own_pin(
    cli_branch: str, pins: tuple[str, ...], caplog: pytest.LogCaptureFixture
) -> None:
    services = three_bundles_on_their_own_branches()
    tool = zero_hit_router(*services, suggestions=FALLBACK_ON)
    out, direct = await serve_api_then_direct_any(
        tool, api_request(project=""), caplog, answering=services, branch=cli_branch
    )
    for svc, pin in zip(services, pins, strict=True):
        member_queries, (fallback_docs, direct_docs) = recorded_queries(svc)
        api_pass, fallback_api, direct_api = member_queries
        # This bundle ran the API pass on its own pin, and the fallback reached
        # it on that same pin, exactly as a direct any union does.
        assert api_pass.branch == pin, svc.project.name
        assert fallback_docs == direct_docs and fallback_api == direct_api == api_pass
    assert_served_exactly_the_direct_any(out, direct, ["chunk"] * 3)


@pytest.mark.parametrize(
    ("cli_branch", "pin"), [("", "trunk"), ("dev", "dev")], ids=["default-branch", "cli-branch"]
)
async def test_a_named_project_sends_the_fallback_to_that_bundle_only(
    cli_branch: str, pin: str, caplog: pytest.LogCaptureFixture
) -> None:
    alpha, beta, gamma = three_bundles_on_their_own_branches()
    tool = zero_hit_router(alpha, beta, gamma, suggestions=FALLBACK_ON)
    out, direct = await serve_api_then_direct_any(
        tool, api_request(project="beta"), caplog, answering=(beta,), branch=cli_branch
    )
    (api_pass, fallback_api, direct_api), (fallback_docs, direct_docs) = recorded_queries(beta)
    assert api_pass.branch == pin
    assert fallback_docs == direct_docs and fallback_api == direct_api == api_pass
    assert recorded_queries(alpha) == recorded_queries(gamma) == ([], [])
    assert_served_exactly_the_direct_any(out, direct, ["chunk"])
    assert out.response.meta["project"] == "beta" and out.response.meta["branch"] == pin


# ── Dependency decisions (#346): the same exclusion a direct any carries ──

DEPENDENCY_DECISION_CASES = {
    # (bundle holds dependency decisions, selectors, exclusion expected)
    "holds-default-request": (True, {}, True),
    "holds-scope-deps": (True, {"scope": "deps"}, True),
    "holds-package-names-the-dependency": (True, {"package": "fastapi"}, False),
    "holds-none": (False, {}, False),
    "holds-none-scope-deps": (False, {"scope": "deps"}, False),
}


@pytest.mark.parametrize(
    ("holds_dependency_decisions", "selectors", "excluded"),
    list(DEPENDENCY_DECISION_CASES.values()),
    ids=list(DEPENDENCY_DECISION_CASES),
)
async def test_the_fallback_docs_query_carries_the_dependency_decision_rule_of_a_direct_any(
    holds_dependency_decisions: bool,
    selectors: dict[str, Any],
    excluded: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    services = recording_bundle(
        "solo", two_branch_directory(), holds_dependency_decisions=holds_dependency_decisions
    )
    tool = zero_hit_router(services, suggestions=FALLBACK_ON)
    out, direct = await serve_api_then_direct_any(
        tool, api_request(**selectors), caplog, answering=(services,)
    )
    (api_pass, fallback_api, _direct_api), (fallback_docs, direct_docs) = recorded_queries(services)
    assert api_pass.exclude_dependency_decisions is excluded
    assert fallback_docs == direct_docs and fallback_api == api_pass
    assert fallback_docs.exclude_dependency_decisions is excluded
    assert_served_exactly_the_direct_any(out, direct, ["chunk"])


async def test_a_union_fallback_applies_each_bundle_its_own_dependency_decision_rule(
    caplog: pytest.LogCaptureFixture,
) -> None:
    holding = recording_bundle("alpha", two_branch_directory(), holds_dependency_decisions=True)
    stock = recording_bundle("beta", two_branch_directory())
    tool = zero_hit_router(holding, stock, suggestions=FALLBACK_ON)
    out, direct = await serve_api_then_direct_any(
        tool, api_request(), caplog, answering=(holding, stock)
    )
    for svc, excluded in ((holding, True), (stock, False)):
        (api_pass, _fallback, _direct), (fallback_docs, direct_docs) = recorded_queries(svc)
        assert fallback_docs == direct_docs
        assert api_pass.exclude_dependency_decisions is excluded, svc.project.name
        assert fallback_docs.exclude_dependency_decisions is excluded, svc.project.name
    assert_served_exactly_the_direct_any(out, direct, ["chunk", "chunk"])


# ── A client-input error is never relabelled as a fallback failure ────────


@pytest.mark.parametrize(
    ("selectors", "cli_branch", "offending_value"),
    [({"project": "nope"}, "", "nope"), ({}, "no-such-branch", "no-such-branch")],
    ids=["unknown-project", "unknown-branch"],
)
async def test_a_bad_selector_on_kind_api_raises_invalid_argument_and_issues_no_query(
    selectors: dict[str, Any],
    cli_branch: str,
    offending_value: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    services = three_bundles_on_their_own_branches()
    tool = zero_hit_router(*services, suggestions=FALLBACK_ON)
    caplog.clear()
    with (
        caplog.at_level("INFO", logger=SUGGESTIONS_LOGGER),
        pytest.raises(InvalidArgumentError) as caught,
    ):
        await tool.search_codebase(api_request(**selectors), branch=cli_branch)
    # The client's own error, naming its own value — not a wrapped fallback failure.
    assert offending_value in str(caught.value)
    assert not isinstance(caught.value, ServiceUnavailableError)
    assert not isinstance(caught.value.__cause__, ServiceUnavailableError)
    assert [recorded_queries(svc) for svc in services] == [([], [])] * 3  # no pass at all
    assert [r for r in caplog.records if r.name == SUGGESTIONS_LOGGER] == []


# ── Determinism (R14): the same request twice, the same response ──────────


@pytest.mark.parametrize(
    ("docs_rows", "expected_suggestion"),
    [(ranked_chunk_rows(3), F_SERVED), ((), F_EMPTY)],
    ids=["served", "empty"],
)
@pytest.mark.parametrize("bundle_count", [1, 3], ids=["single", "union"])
async def test_the_same_api_zero_hit_twice_gives_the_same_response(
    docs_rows: tuple[Chunk, ...],
    expected_suggestion: str,
    bundle_count: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    services = tuple(
        solo_service(RecordingDocsSearch(docs_rows), RecordingApiSearch(), project=svc.project)
        for svc in (make_service(name) for name in ("alpha", "beta", "gamma")[:bundle_count])
    )
    tool = zero_hit_router(*services, suggestions=FALLBACK_ON)
    first, second = [await serve_search(tool, api_request(limit=2), caplog) for _ in range(2)]
    # Requested kind: each request ran its own API pass (member-only) on every bundle.
    assert all(len(recorded_queries(s)[0]) == len(recorded_queries(s)[1]) + 2 for s in services)
    assert F_RULE in first.rules and first.rules == second.rules  # raw API zero hit
    assert first.response.meta["suggestion"] == expected_suggestion  # served kind
    assert (first.response.text, first.response.items) == (
        second.response.text,
        second.response.items,
    )
    assert first.response.meta == second.response.meta
    assert first.response.structured() == second.response.structured()
    assert first.rendered_rows == second.rendered_rows
