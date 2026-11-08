"""KAF-04 (R2): end-to-end branch preservation at the public boundary.

Review found that the ``selector = branch or _branch_selector(payload)``
fix on ``ToolRouter.search_codebase`` (landed in commit ``35ccf337``) was
only pinned by a renderer-level unit test
(``test_api_zero_hit_renderer_preserves_non_default_branch`` in
``test_error_empty_contract.py``). The public boundary — a
``ToolRouter.search_codebase(payload, branch="feature-x")`` call against a
populated ``FakeBranchDirectory`` — had no regression test.

This file adds ONE end-to-end test that serves a ``kind="api"`` zero hit
through that public seam with branch + scope + package + project all
jointly non-default, and asserts the rendered retry preserves ALL FOUR
selectors verbatim in both the body ``[suggestion:`` line and
``meta.suggestion`` — each exactly once.

KNOWN INTERFACE GAP (awaiting #315): ``SearchInput`` has no ``branch``
field today. The retry rendering is a readable Python-kwarg-style
template; an MCP client cannot re-issue a ``branch=`` argument against the
current ``SearchInput`` schema. This test therefore exercises the CLI-kwarg
seam (``router.search_codebase(payload, branch="feature-x")``), NOT a
forged ``payload.branch``. The query / scope / package / project half of
the rendered call round-trips against today's ``SearchInput`` and IS
executable via MCP; the ``branch=`` fragment is a readable template for the
CLI/#315 flow only. Do NOT paper the gap over by inventing a schema field.
"""

from __future__ import annotations

import dataclasses

from pydocs_mcp.application.branch_directory import BranchSnapshot
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
)
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.models import BranchIndexSource, Chunk, ModuleMemberList, SearchResponse
from pydocs_mcp.retrieval.config import SuggestionsConfig
from pydocs_mcp.storage.branch_records import BranchRecord

from ._router_fakes import (
    FakeBranchDirectory,
    FakeDocs,
    RecordingApiSearch,
    RecordingDocsSearch,
    make_envelope,
    make_service,
)


_WITNESS_QUERY = "nested serialization"
_HEAD = "a" * 40


def _branch(name: str, is_default: bool = False) -> BranchRecord:
    return BranchRecord(
        name,
        _HEAD,
        BranchIndexSource.WORKING_TREE,
        "p",
        1.0,
        1.0,
        is_default=is_default,
    )


class _EmptyApi:
    """API search that always returns zero rows — the zero-hit trigger."""

    async def search(self, query):
        return SearchResponse(result=ModuleMemberList(items=()), query=query, duration_ms=0.0)

    async def ranked(self, query):
        return ModuleMemberList(items=())


def _snapshot_with_feature_x() -> BranchSnapshot:
    """Two indexed branches — the default ``main`` and the alternate ``feature-x`` —
    so ``branch="feature-x"`` resolves without raising on the name check."""
    rows = (_branch("main", is_default=True), _branch("feature-x"))
    return BranchSnapshot(rows, "main", "feature-x", {"feature-x": _HEAD})


def _service_with_branches() -> ProjectServices:
    """One ``solo`` project whose api service returns zero rows, with the
    two-branch directory above. The docs service uses the shared empty docs
    fake — a ``kind="api"`` request routes through the API path alone, so an
    empty docs service here is cosmetic, but keeps the fixture total-empty."""
    base = make_service(name="solo")
    return dataclasses.replace(
        base,
        docs=FakeDocs(),  # unused on kind="api", but keeps the fixture empty-safe
        api=_EmptyApi(),
        branch_directory=FakeBranchDirectory(_snapshot_with_feature_x()),
    )


def _end_to_end_router(
    service: ProjectServices | None = None, *, fallback: bool = False
) -> ToolRouter:
    """``fallback`` is the ADR 0027 zero-hit fallback flag: OFF (named) for the
    #438 retry-hint pin, ON for its twin."""
    services = (service or _service_with_branches(),)
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
        suggestions=SuggestionsConfig(api_zero_hit_fallback_any=fallback),
    )


async def test_api_zero_hit_end_to_end_preserves_branch_scope_package_project() -> None:
    """ONE call pins the WHOLE selector tuple together through the public boundary.

    The ``branch="feature-x"`` kwarg flows through
    ``ToolRouter.search_codebase(payload, branch="feature-x")`` into the
    kind-aware retry renderer (the ``35ccf337`` fix), and the three selectors on
    the payload (``scope="deps"``, ``package="fastapi"``, ``project="solo"``)
    ride alongside — each appearing exactly once in both the body
    ``[suggestion:`` line and ``meta.suggestion``.
    """
    router = _end_to_end_router()
    payload = SearchInput(
        query=_WITNESS_QUERY,
        kind="api",
        scope="deps",
        package="fastapi",
        project="solo",
    )

    response = await router.search_codebase(payload, branch="feature-x")

    # Zero hit still returned (engine semantics unchanged by KAF-02/KAF-04).
    assert len(response.items) == 0
    assert "No symbols found." in response.text

    # The retry body line carries exactly one of each selector fragment.
    retry_lines = [line for line in response.text.splitlines() if line.startswith("[suggestion:")]
    assert len(retry_lines) == 1
    retry_line = retry_lines[0]
    assert retry_line.count('branch="feature-x"') == 1
    assert retry_line.count('scope="deps"') == 1
    assert retry_line.count('package="fastapi"') == 1
    assert retry_line.count('project="solo"') == 1

    # meta.suggestion carries the SAME kind-aware string (precedence rule:
    # the kind-aware rule wins the single-string meta field when both fire).
    suggestion = response.meta["suggestion"]
    assert suggestion.count('branch="feature-x"') == 1
    assert suggestion.count('scope="deps"') == 1
    assert suggestion.count('package="fastapi"') == 1
    assert suggestion.count('project="solo"') == 1

    # meta.branch reflects the resolved branch — the public contract that
    # ``branch="feature-x"`` was answered under, not a silent fall-back to
    # ``main``.
    assert response.meta["branch"] == "feature-x"


_SERVED = (
    '[suggestion: kind="api" matched nothing — showing kind="any" results '
    "for the same query and filters]"
)


async def test_api_zero_hit_fallback_runs_on_the_same_branch_and_selectors() -> None:
    """ADR 0027 twin: the same call with the fallback ON. The docs half of the
    ``kind="any"`` fallback receives the API pass's exact query, corpus
    pre-filter, ``limit`` and the CLI ``branch="feature-x"`` pin — and the
    served rows are labelled, never presented as symbol hits."""
    docs = RecordingDocsSearch((Chunk(text="nested serialization", id=1, relevance=0.5),))
    api = RecordingApiSearch()
    service = dataclasses.replace(_service_with_branches(), docs=docs, api=api)
    payload = SearchInput(
        query=_WITNESS_QUERY, kind="api", scope="deps", package="fastapi", project="solo"
    )

    response = await _end_to_end_router(service, fallback=True).search_codebase(
        payload, branch="feature-x"
    )

    api_pass, fallback_docs = api.queries[0], docs.queries[0]
    assert len(docs.queries) == 1 and len(api.queries) == 2
    for query in (fallback_docs, api.queries[1]):
        assert (query.terms, query.pre_filter) == (api_pass.terms, api_pass.pre_filter)
        assert (query.branch, query.max_results) == ("feature-x", api_pass.max_results)
    assert response.text.split("\n")[2] == _SERVED
    assert response.meta["suggestion"] == _SERVED
    assert [item["kind"] for item in response.items] == ["chunk"]
    assert response.meta["branch"] == "feature-x"
