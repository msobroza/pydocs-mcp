"""``search_codebase`` answers from the branch the request resolves to (spec §6.4, #312).

The router resolves the selector once (#311) and the search runs pinned to that
branch — the default selector included, so a bundle holding a second branch
never mixes it into the default answer and ``meta.branch`` names the branch
whose rows were served. A one-branch bundle pins nothing: its queries stay
exactly what they were. On a multi-repo union the answering bundle pins the
request's branch and every other bundle its own default resolution.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from pydocs_mcp.application.branch_directory import BranchSnapshot
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
)
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.models import (
    NON_GIT_BRANCH_NAME,
    BranchIndexSource,
    ChunkList,
    LandingKind,
    ModuleMemberList,
    SearchQuery,
    SearchResponse,
)
from pydocs_mcp.storage.branch_records import BranchRecord

from ._router_fakes import FakeBranchDirectory, make_envelope, make_service

MAIN, FEATURE = "main", "feature/x"
UNIT = "1234567" + "0" * 33


def _row(name: str, **kw: object) -> BranchRecord:
    return BranchRecord(name, "a" * 40, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0, **kw)


def _directory(*names: str, live: str | None = MAIN) -> FakeBranchDirectory:
    rows = tuple(_row(n, is_default=(i == 0)) for i, n in enumerate(names))
    return FakeBranchDirectory(BranchSnapshot(rows, names[0], live, {}))


@dataclass
class _RecordingDocs:
    queries: list[SearchQuery] = field(default_factory=list)

    async def search(self, query: SearchQuery) -> SearchResponse:
        self.queries.append(query)
        return SearchResponse(result=ChunkList(items=()), query=query, duration_ms=0.0)

    async def ranked(self, query: SearchQuery) -> ChunkList:
        self.queries.append(query)
        return ChunkList(items=())


@dataclass
class _RecordingApi:
    queries: list[SearchQuery] = field(default_factory=list)

    async def search(self, query: SearchQuery) -> SearchResponse:
        self.queries.append(query)
        return SearchResponse(result=ModuleMemberList(items=()), query=query, duration_ms=0.0)

    async def ranked(self, query: SearchQuery) -> ModuleMemberList:
        self.queries.append(query)
        return ModuleMemberList(items=())


def _service(name: str, directory: FakeBranchDirectory) -> ProjectServices:
    return dataclasses.replace(
        make_service(name),
        docs=_RecordingDocs(),
        api=_RecordingApi(),
        branch_directory=directory,
    )


def _router(*services: ProjectServices) -> ToolRouter:
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


def _branches_searched(svc: ProjectServices) -> list[str]:
    return [q.branch for q in (*svc.docs.queries, *svc.api.queries)]  # type: ignore[attr-defined]


async def test_the_default_search_pins_the_resolved_branch_and_meta_names_it() -> None:
    directory = _directory(MAIN, FEATURE)
    svc = _service("solo", directory)
    response = await _router(svc).search_codebase(SearchInput(query="q"))
    assert _branches_searched(svc) == [MAIN, MAIN]  # the chunk and the member search
    assert response.meta["branch"] == MAIN
    assert directory.snapshots == 1  # resolved once, the pin read off the resolution


async def test_a_named_branch_is_searched_through_the_same_resolver() -> None:
    svc = _service("solo", _directory(MAIN, FEATURE))
    response = await _router(svc).search_codebase(
        SearchInput(query="q", kind="docs"), branch=FEATURE
    )
    assert _branches_searched(svc) == [FEATURE] and response.meta["branch"] == FEATURE


async def test_a_one_branch_bundle_searches_exactly_as_before() -> None:
    svc = _service("solo", _directory(MAIN))
    response = await _router(svc).search_codebase(SearchInput(query="q", kind="api"))
    assert _branches_searched(svc) == [""] and response.meta["branch"] == MAIN


async def test_a_project_outside_git_pins_nothing() -> None:
    svc = _service("solo", _directory(NON_GIT_BRANCH_NAME, live=None))
    response = await _router(svc).search_codebase(SearchInput(query="q", kind="docs"))
    assert _branches_searched(svc) == [""] and response.meta["branch"] is None


async def test_each_unioned_bundle_searches_its_own_resolved_branch() -> None:
    alpha = _service("alpha", _directory(MAIN, FEATURE, live=FEATURE))
    beta = _service("beta", _directory("trunk", "dev", live="trunk"))
    solo = _service("solo", _directory(MAIN))
    await _router(alpha, beta, solo).search_codebase(SearchInput(query="q", kind="docs"))
    assert _branches_searched(alpha) == [FEATURE]
    assert _branches_searched(beta) == ["trunk"]
    assert _branches_searched(solo) == [""]


async def test_a_named_branch_on_a_union_pins_the_answering_bundle_only() -> None:
    """The union rule (plan Task 14 Step 6): the request's selector resolves on
    the answering bundle — the one ``meta.project`` and ``meta.branch`` describe
    — and every other bundle answers from its own default resolution, so a
    bundle that never indexed the name cannot fail the whole union, and one
    that also holds the name still answers from its default (per-bundle named
    selection is #315's)."""
    alpha = _service("alpha", _directory(MAIN, FEATURE))
    beta = _service("beta", _directory("trunk", "dev", live="trunk"))
    gamma = _service("gamma", _directory("trunk", FEATURE, live="trunk"))
    response = await _router(alpha, beta, gamma).search_codebase(
        SearchInput(query="q", kind="docs"), branch=FEATURE
    )
    assert _branches_searched(alpha) == [FEATURE] and response.meta["branch"] == FEATURE
    assert _branches_searched(beta) == ["trunk"] and _branches_searched(gamma) == ["trunk"]


@dataclass
class _Decisions:
    async def search_with_items(self, query: str, **_scope: object):
        return "decisions", (), {}


async def test_a_decision_union_resolves_no_other_bundle() -> None:
    """``kind="decision"`` has no union path and stays unpinned (#313): only the
    answering bundle resolves, for ``meta.branch``."""
    alpha_directory, beta_directory = _directory(MAIN, FEATURE), _directory("trunk")
    alpha = dataclasses.replace(_service("alpha", alpha_directory), decisions=_Decisions())
    beta = dataclasses.replace(_service("beta", beta_directory), decisions=_Decisions())
    await _router(alpha, beta).search_codebase(
        SearchInput(query="q", kind="decision"), branch=FEATURE
    )
    assert alpha_directory.snapshots == 1 and beta_directory.snapshots == 0


async def test_a_landing_unit_pins_its_own_empty_tree() -> None:
    """A unit carries no tree rows (§6.5b), so no project row can answer for it;
    the §6.11 empty-result split with its suggestion lands with #315."""
    unit = BranchRecord(
        UNIT, UNIT, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0,
        landing_kind=LandingKind.SINGLE_COMMIT,
    )  # fmt: skip
    rows = (_row(MAIN, is_default=True), unit)
    svc = _service("solo", FakeBranchDirectory(BranchSnapshot(rows, MAIN, MAIN, {})))
    response = await _router(svc).search_codebase(
        SearchInput(query="q", kind="docs"), branch="1234567"
    )
    assert _branches_searched(svc) == [UNIT] and response.meta["branch"] == UNIT
