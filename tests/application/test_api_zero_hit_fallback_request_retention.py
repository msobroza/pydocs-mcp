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
the requested kind (the API pass's member query ran), the raw API zero hit
(the fired rule) and the served kind (the label plus each row's own ``kind``).
The pinned label bytes and the shared helpers come from that matrix module.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from pydocs_mcp.application.branch_directory import BranchSnapshot
from pydocs_mcp.application.mcp_errors import InvalidArgumentError
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.multi_project_search import ProjectServices
from pydocs_mcp.models import BranchIndexSource, Chunk, ModuleMember
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
    api_request,
    direct_any,
    fallback_flags,
    serve_search,
    solo_service,
    without_label,
    zero_hit_router,
)

FALLBACK_ON = fallback_flags(True)
# Spec D12's fixed failure prefix, which a client-input error must never carry.
API_ZERO_HIT_FALLBACK_FAILURE_TEXT = (
    'search_codebase(kind="api") found no symbols; its kind="any" fallback failed'
)


def chunk_rows(count: int) -> tuple[Chunk, ...]:
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


def member_rows(count: int) -> tuple[ModuleMember, ...]:
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


def item_kinds(items: tuple[dict[str, Any], ...]) -> list[str]:
    return [item["kind"] for item in items]


# ── A small limit caps each listing, as for a direct kind="any" ───────────


async def test_a_small_limit_caps_each_fallback_listing_like_a_direct_any(
    caplog: pytest.LogCaptureFixture,
) -> None:
    docs = RecordingDocsSearch(chunk_rows(5))
    api = MembersAfterFirstQueryApiSearch(member_rows(5))
    tool = zero_hit_router(solo_service(docs, api), suggestions=FALLBACK_ON)
    request = api_request(limit=3)
    out = await serve_search(tool, request, caplog)
    direct = await serve_search(tool, direct_any(request), caplog)
    # Requested kind: the API pass's member query ran first and found nothing.
    assert request.kind == "api" and api.queries[0].max_results == 3
    # Raw API zero hit: the fallback rule fired.
    assert out.rules == [F_RULE]
    # Served kind: the label, then at most 3 chunk rows plus at most 3 member rows.
    assert out.response.text.split("\n")[:3] == [HEADER, "", F_SERVED]
    assert item_kinds(out.response.items) == ["chunk"] * 3 + ["member"] * 3
    assert out.response.items == direct.response.items
    assert without_label(out.response.text) == direct.response.text
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


def _searched_terms(docs: RecordingDocsSearch, api: Any) -> list[str]:
    return [q.terms for q in (*api.queries, *docs.queries)]


@pytest.mark.parametrize("query", list(AWKWARD_QUERIES.values()), ids=list(AWKWARD_QUERIES))
async def test_an_awkward_query_reaches_both_passes_verbatim_under_the_served_label(
    query: str, caplog: pytest.LogCaptureFixture
) -> None:
    docs, api = RecordingDocsSearch(chunk_rows(2)), MembersAfterFirstQueryApiSearch(())
    tool = zero_hit_router(solo_service(docs, api), suggestions=FALLBACK_ON)
    request = SearchInput(query=query, kind="api")
    out = await serve_search(tool, request, caplog)
    # Requested kind + exact bytes: the API pass, then the fallback's chunk and
    # member queries, each searched the original string.
    assert request.kind == "api" and _searched_terms(docs, api) == [query] * 3
    assert out.rules == [F_RULE]  # raw API zero hit
    # Served kind: the byte-constant label leads the body, rows keep their kind.
    assert out.response.text.split("\n")[:3] == [HEADER, "", F_SERVED]
    assert out.response.meta["suggestion"] == F_SERVED
    assert item_kinds(out.response.items) == ["chunk", "chunk"]
    direct = await serve_search(tool, direct_any(request), caplog)
    assert _searched_terms(docs, api) == [query] * 5
    assert without_label(out.response.text) == direct.response.text
    assert out.response.items == direct.response.items


@pytest.mark.parametrize("query", list(AWKWARD_QUERIES.values()), ids=list(AWKWARD_QUERIES))
async def test_an_awkward_query_reaches_both_passes_verbatim_under_the_empty_label(
    query: str, caplog: pytest.LogCaptureFixture
) -> None:
    docs, api = RecordingDocsSearch(), MembersAfterFirstQueryApiSearch(())
    tool = zero_hit_router(solo_service(docs, api), suggestions=FALLBACK_ON)
    request = SearchInput(query=query, kind="api")
    out = await serve_search(tool, request, caplog)
    assert request.kind == "api" and _searched_terms(docs, api) == [query] * 3
    assert out.rules == [Z_RULE, F_RULE]  # raw API zero hit (Z on, then F)
    # Nothing served: the whole text is fixed bytes, whatever the query held.
    assert out.response.text == f"{HEADER}\n\nNo symbols found.\n{POINTER}\n\n{F_EMPTY}\n"
    assert out.response.meta["suggestion"] == F_EMPTY and out.response.items == ()


# ── Selectors and the branch pin reach the fallback unchanged ─────────────

MAIN, FEATURE = "main", "feature/x"


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


SELECTOR_CASES = {
    "defaults": ({}, "", {"scope": "all"}, MAIN, 10),
    "scope-deps": ({"scope": "deps"}, "", {"scope": "dependencies_only"}, MAIN, 10),
    "scope-project": ({"scope": "project"}, "", {"scope": "project_only"}, MAIN, 10),
    "package": ({"package": "fastapi"}, "", {"scope": "all", "package": "fastapi"}, MAIN, 10),
    "project": ({"project": "solo"}, "", {"scope": "all"}, MAIN, 10),
    "cli-branch": ({}, FEATURE, {"scope": "all"}, FEATURE, 10),
    "all-at-once": (
        {"scope": "deps", "package": "fastapi", "project": "solo", "limit": 7},
        FEATURE,
        {"scope": "dependencies_only", "package": "fastapi"},
        FEATURE,
        7,
    ),
}


@pytest.mark.parametrize(
    ("selectors", "cli_branch", "pre_filter", "pin", "max_results"),
    list(SELECTOR_CASES.values()),
    ids=list(SELECTOR_CASES),
)
async def test_the_fallback_query_carries_the_api_pass_selectors_and_branch_pin(
    selectors: dict[str, Any],
    cli_branch: str,
    pre_filter: dict[str, str],
    pin: str,
    max_results: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    docs, api = RecordingDocsSearch(chunk_rows(1)), MembersAfterFirstQueryApiSearch(())
    services = solo_service(docs, api, branch_directory=two_branch_directory())
    tool = zero_hit_router(services, suggestions=FALLBACK_ON)
    request = SearchInput(query=QUERY, kind="api", **selectors)
    out = await serve_search(tool, request, caplog, branch=cli_branch)
    direct = await serve_search(tool, direct_any(request), caplog, branch=cli_branch)
    api_pass, fallback_api, direct_api = api.queries
    fallback_docs, direct_docs = docs.queries
    # Requested kind: the API pass ran on exactly the request's slice and pin.
    assert request.kind == "api"
    assert (api_pass.pre_filter, api_pass.branch, api_pass.max_results) == (
        pre_filter,
        pin,
        max_results,
    )
    # The fallback's two queries are the API pass's, and a direct any's, exactly.
    assert fallback_docs == fallback_api == api_pass == direct_docs == direct_api
    assert out.rules == [F_RULE] and direct.rules == []  # raw API zero hit
    assert out.response.meta["suggestion"] == F_SERVED  # served kind…
    assert item_kinds(out.response.items) == ["chunk"]  # …and each row's own kind
    assert out.response.meta["branch"] == direct.response.meta["branch"] == pin


# ── Union and named project: every bundle on its own pin (#312) ───────────


def recording_bundle(name: str, directory: FakeBranchDirectory, **replace: Any) -> ProjectServices:
    """One loaded bundle serving one chunk row of its own, members only after
    the first query, with ``directory`` as its branch dimension."""
    docs = RecordingDocsSearch(
        tuple(
            dataclasses.replace(c, id=c.id + len(name), metadata={**c.metadata, "title": name})
            for c in chunk_rows(1)
        )
    )
    return dataclasses.replace(
        make_service(name),
        docs=docs,
        api=MembersAfterFirstQueryApiSearch(()),
        branch_directory=directory,
        **replace,
    )


def three_bundles() -> tuple[ProjectServices, ...]:
    """``alpha`` answers (first loaded); ``beta`` never indexed ``feature/x``;
    ``gamma`` did, but defaults to ``trunk``."""
    return (
        recording_bundle("alpha", two_branch_directory(MAIN, FEATURE)),
        recording_bundle("beta", two_branch_directory("trunk", "dev")),
        recording_bundle("gamma", two_branch_directory("trunk", FEATURE)),
    )


def queries_of(svc: ProjectServices) -> tuple[list[Any], list[Any]]:
    return svc.api.queries, svc.docs.queries  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("cli_branch", "pins"),
    [("", (MAIN, "trunk", "trunk")), (FEATURE, (FEATURE, "trunk", "trunk"))],
    ids=["default-branch", "cli-branch"],
)
async def test_a_union_fallback_reaches_every_bundle_on_its_own_pin(
    cli_branch: str, pins: tuple[str, ...], caplog: pytest.LogCaptureFixture
) -> None:
    services = three_bundles()
    tool = zero_hit_router(*services, suggestions=FALLBACK_ON)
    request = api_request(project="")
    out = await serve_search(tool, request, caplog, branch=cli_branch)
    direct = await serve_search(tool, direct_any(request), caplog, branch=cli_branch)
    for svc, pin in zip(services, pins, strict=True):
        (api_pass, fallback_api, direct_api), (fallback_docs, direct_docs) = queries_of(svc)
        # Requested kind: this bundle ran the API pass on its own pin.
        assert api_pass.branch == pin, svc.project.name
        # The fallback reached it on that same pin, exactly as a direct any union.
        assert fallback_docs == direct_docs and fallback_api == direct_api == api_pass
    assert out.rules == [F_RULE]  # raw API zero hit
    assert out.response.text.split("\n")[:3] == [HEADER, "", F_SERVED]  # served kind
    assert item_kinds(out.response.items) == ["chunk"] * 3
    assert out.response.items == direct.response.items
    assert without_label(out.response.text) == direct.response.text


@pytest.mark.parametrize(
    ("cli_branch", "pin"), [("", "trunk"), ("dev", "dev")], ids=["default-branch", "cli-branch"]
)
async def test_a_named_project_sends_the_fallback_to_that_bundle_only(
    cli_branch: str, pin: str, caplog: pytest.LogCaptureFixture
) -> None:
    alpha, beta, gamma = three_bundles()
    tool = zero_hit_router(alpha, beta, gamma, suggestions=FALLBACK_ON)
    request = api_request(project="beta")
    out = await serve_search(tool, request, caplog, branch=cli_branch)
    direct = await serve_search(tool, direct_any(request), caplog, branch=cli_branch)
    (api_pass, fallback_api, direct_api), (fallback_docs, direct_docs) = queries_of(beta)
    assert request.kind == "api" and api_pass.branch == pin  # requested kind, its pin
    assert fallback_docs == direct_docs and fallback_api == direct_api == api_pass
    assert queries_of(alpha) == queries_of(gamma) == ([], [])
    assert out.rules == [F_RULE]  # raw API zero hit
    assert out.response.meta["suggestion"] == F_SERVED  # served kind
    assert [item["kind"] for item in out.response.items] == ["chunk"]
    assert out.response.items == direct.response.items
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
    ("holds", "selectors", "excluded"),
    list(DEPENDENCY_DECISION_CASES.values()),
    ids=list(DEPENDENCY_DECISION_CASES),
)
async def test_the_fallback_docs_query_carries_the_dependency_decision_rule_of_a_direct_any(
    holds: bool, selectors: dict[str, Any], excluded: bool, caplog: pytest.LogCaptureFixture
) -> None:
    docs, api = RecordingDocsSearch(chunk_rows(1)), MembersAfterFirstQueryApiSearch(())
    tool = zero_hit_router(
        solo_service(docs, api, holds_dependency_decisions=holds), suggestions=FALLBACK_ON
    )
    request = api_request(**selectors)
    out = await serve_search(tool, request, caplog)
    await serve_search(tool, direct_any(request), caplog)
    fallback_docs, direct_docs = docs.queries
    api_pass, fallback_api, _direct_api = api.queries
    assert request.kind == "api" and api_pass.exclude_dependency_decisions is excluded
    assert fallback_docs == direct_docs and fallback_api == api_pass
    assert fallback_docs.exclude_dependency_decisions is excluded
    assert out.rules == [F_RULE]  # raw API zero hit
    assert out.response.meta["suggestion"] == F_SERVED  # served kind
    assert item_kinds(out.response.items) == ["chunk"]


async def test_a_union_fallback_applies_each_bundle_its_own_dependency_decision_rule(
    caplog: pytest.LogCaptureFixture,
) -> None:
    holding = recording_bundle("alpha", two_branch_directory(), holds_dependency_decisions=True)
    stock = recording_bundle("beta", two_branch_directory())
    tool = zero_hit_router(holding, stock, suggestions=FALLBACK_ON)
    out = await serve_search(tool, api_request(), caplog)
    await serve_search(tool, direct_any(api_request()), caplog)
    for svc, excluded in ((holding, True), (stock, False)):
        _api, (fallback_docs, direct_docs) = queries_of(svc)
        assert fallback_docs == direct_docs
        assert fallback_docs.exclude_dependency_decisions is excluded, svc.project.name
    assert out.rules == [F_RULE] and out.response.meta["suggestion"] == F_SERVED
    assert item_kinds(out.response.items) == ["chunk", "chunk"]


# ── A client-input error is never relabelled as a fallback failure ────────


@pytest.mark.parametrize(
    ("selectors", "cli_branch"),
    [({"project": "nope"}, ""), ({}, "no-such-branch")],
    ids=["unknown-project", "unknown-branch"],
)
async def test_a_bad_selector_on_kind_api_raises_invalid_argument_and_issues_no_query(
    selectors: dict[str, Any], cli_branch: str, caplog: pytest.LogCaptureFixture
) -> None:
    services = three_bundles()
    tool = zero_hit_router(*services, suggestions=FALLBACK_ON)
    caplog.clear()
    with caplog.at_level("INFO", logger="pydocs_mcp.application.suggestions"):
        with pytest.raises(InvalidArgumentError) as caught:
            await tool.search_codebase(api_request(**selectors), branch=cli_branch)
    assert not str(caught.value).startswith(API_ZERO_HIT_FALLBACK_FAILURE_TEXT)
    assert [queries_of(svc) for svc in services] == [([], [])] * 3  # no pass at all
    assert [r for r in caplog.records if r.name == "pydocs_mcp.application.suggestions"] == []


# ── Determinism (R14): the same request twice, the same response ──────────


@pytest.mark.parametrize(
    ("docs_rows", "expected_suggestion"),
    [(chunk_rows(3), F_SERVED), ((), F_EMPTY)],
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
        dataclasses.replace(
            make_service(name), docs=RecordingDocsSearch(docs_rows), api=RecordingApiSearch()
        )
        for name in ("alpha", "beta", "gamma")[:bundle_count]
    )
    tool = zero_hit_router(*services, suggestions=FALLBACK_ON)
    first = await serve_search(tool, api_request(limit=2), caplog)
    second = await serve_search(tool, api_request(limit=2), caplog)
    # Requested kind: each request ran its own API pass on every bundle.
    assert all(len(svc.api.queries) == 4 for svc in services)  # type: ignore[attr-defined]
    assert F_RULE in first.rules and first.rules == second.rules  # raw API zero hit
    assert first.response.meta["suggestion"] == expected_suggestion  # served kind
    assert first.response.text == second.response.text
    assert first.response.items == second.response.items
    assert first.response.meta == second.response.meta
    assert first.rendered_rows == second.rendered_rows
    assert first.response.structured() == second.response.structured()
