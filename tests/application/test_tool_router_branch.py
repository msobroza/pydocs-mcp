"""ToolRouter resolves the ``branch`` selector once per call and hands the
resolution — and the answering project's own probe — to the envelope
(spec §6.4, §6.7, §6.11; #311, O19)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from pydocs_mcp.application.branch_directory import BranchSnapshot
from pydocs_mcp.application.freshness import EnvelopeInfo
from pydocs_mcp.application.mcp_errors import InvalidArgumentError
from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    GlobInput,
    GrepInput,
    OverviewInput,
    ReadFileInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
    WhyInput,
)
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
)
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.models import BranchIndexSource, LandingKind
from pydocs_mcp.retrieval.config import SuggestionsConfig
from pydocs_mcp.storage.branch_records import BranchRecord

from ._router_fakes import (
    BranchSelectedInput,
    CountingProbe,
    FakeBranchDirectory,
    FakeFileTools,
    make_envelope,
    make_service,
)

A, B, UNIT = "a" * 40, "b" * 40, "1234567" + "0" * 33

# (router method, input) — every one of the nine tools, get_symbol's source
# depth included (it takes its own routing path).
_NINE_TOOLS: tuple[tuple[str, Any], ...] = (
    ("get_overview", OverviewInput()),
    ("search_codebase", SearchInput(query="x")),
    ("get_symbol", SymbolInput(target="pkg.mod.X")),
    ("get_symbol", SymbolInput(target="pkg.mod.X", depth="source")),
    ("get_context", ContextInput(targets=["pkg.mod.X"])),
    ("get_references", ReferencesInput(target="pkg.mod.X")),
    ("get_why", WhyInput(query="why")),
    ("grep", GrepInput(pattern="x")),
    ("glob", GlobInput(pattern="*.py")),
    ("read_file", ReadFileInput(file_path="a.py")),
)
_IDS = [f"{method}-{i}" for i, (method, _) in enumerate(_NINE_TOOLS)]


def _row(name: str, head: str = A, **kw: object) -> BranchRecord:
    return BranchRecord(name, head, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0, **kw)


def _unit(sha: str) -> BranchRecord:
    return BranchRecord(
        sha,
        sha,
        BranchIndexSource.GIT_OBJECTS,
        "p",
        1.0,
        1.0,
        landing_kind=LandingKind.SINGLE_COMMIT,
    )


def _probe(head: str, *, stale: bool) -> CountingProbe:
    return CountingProbe(EnvelopeInfo(head, B if stale else head, 0, 1, stale, "main"))


class _Decisions:
    async def why_search(self, query: str):
        return f"WHY {query}", (), {}


def _snapshot(live: str | None = "feature/x") -> BranchSnapshot:
    rows = (_row("main", is_default=True), _row("feature/x"), _unit(UNIT))
    return BranchSnapshot(rows, "main", live, {"feature/x": A})


def _service(name: str = "solo", **kw: Any) -> ProjectServices:
    base = make_service(name, files=FakeFileTools(name))
    return dataclasses.replace(base, decisions=_Decisions(), **kw)


def _router(*services: ProjectServices, suggestions: SuggestionsConfig | None = None) -> ToolRouter:
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
        suggestions=suggestions or SuggestionsConfig(),
    )


@pytest.mark.parametrize(("method", "payload"), _NINE_TOOLS, ids=_IDS)
async def test_every_tool_names_the_resolved_branch_in_meta(method: str, payload: Any) -> None:
    directory = FakeBranchDirectory(_snapshot())
    router = _router(_service(branch_directory=directory))
    response = await getattr(router, method)(payload)
    assert response.meta["branch"] == "feature/x"
    assert directory.snapshots == 1 and directory.touched == ["feature/x"]


@pytest.mark.parametrize(("method", "payload"), _NINE_TOOLS, ids=_IDS)
async def test_an_unknown_branch_is_the_tool_level_error_naming_the_indexed_branches(
    method: str, payload: Any
) -> None:
    router = _router(_service(branch_directory=FakeBranchDirectory(_snapshot())))
    with pytest.raises(InvalidArgumentError) as caught:
        await getattr(router, method)(BranchSelectedInput(payload, "nope"))
    assert str(caught.value) == (
        "no indexed branch 'nope'; indexed: ['feature/x', 'main']; "
        "run pydocs-mcp index . --branch nope"
    )


@pytest.mark.parametrize(("method", "payload"), _NINE_TOOLS, ids=_IDS)
async def test_a_sha_that_is_no_landing_here_is_refused_with_the_spec_sentence(
    method: str, payload: Any
) -> None:
    router = _router(_service(branch_directory=FakeBranchDirectory(_snapshot())))
    with pytest.raises(InvalidArgumentError) as caught:
        await getattr(router, method)(BranchSelectedInput(payload, "deadbee"))
    assert str(caught.value) == (
        "no branch or landing unit matches 'deadbee'; landings in the window: ['1234567']"
    )


async def test_a_named_selection_answers_with_that_branch_and_its_own_pair() -> None:
    directory = FakeBranchDirectory(
        BranchSnapshot((_row("main", is_default=True), _row("dev")), "main", "main", {"dev": B})
    )
    router = _router(_service(branch_directory=directory, freshness=_probe(A, stale=False)))
    response = await router.glob(BranchSelectedInput(GlobInput(pattern="*"), "dev"))
    assert response.meta["branch"] == "dev"
    # dev's own pair — indexed at A, its ref now at B — not the checkout's (A, A).
    meta = response.meta
    assert (meta["indexed_git_head"], meta["live_git_head"], meta["index_stale"]) == (A, B, True)
    assert directory.touched == ["dev"]


async def test_a_bundle_without_branch_rows_keeps_meta_branch_null_and_touches_nothing() -> None:
    directory = FakeBranchDirectory(BranchSnapshot((), None, None, {}))
    router = _router(_service(branch_directory=directory))
    response = await router.grep(GrepInput(pattern="x"))
    assert response.meta["branch"] is None and directory.touched == []


async def test_the_checkout_suggestion_reaches_the_suggestion_tools_behind_its_flag() -> None:
    directory = FakeBranchDirectory(_snapshot(live="feature/y"))
    router = _router(_service(branch_directory=directory))
    response = await router.grep(GrepInput(pattern="x"))
    assert response.meta["branch"] == "main"
    assert response.meta["suggestion"] == (
        "[suggestion: checked-out branch 'feature/y' is not indexed; "
        "run: pydocs-mcp index . --branch feature/y]"
    )
    muted = _router(
        _service(branch_directory=directory),
        suggestions=SuggestionsConfig(checkout_not_indexed=False),
    )
    assert "suggestion" not in (await muted.grep(GrepInput(pattern="x"))).meta


async def test_each_project_answers_with_its_own_freshness() -> None:
    """O19: the probe that answers is the named project's, not the first one's."""
    alpha = _service("alpha", freshness=_probe(A, stale=True))
    beta = _service("beta", freshness=_probe(B, stale=False))
    router = _router(alpha, beta)
    on_beta = await router.glob(GlobInput(pattern="*", project="beta"))
    assert on_beta.meta["indexed_git_head"] == B and on_beta.meta["index_stale"] is False
    assert on_beta.text.startswith(f"[index: {B[:7]} ")
    on_alpha = await router.read_file(ReadFileInput(file_path="a.py", project="alpha"))
    assert on_alpha.meta["indexed_git_head"] == A and on_alpha.meta["index_stale"] is True
    # No selector: the first-loaded project, which meta.project names too.
    default = await router.grep(GrepInput(pattern="x"))
    assert default.meta["project"] == "alpha" and default.meta["indexed_git_head"] == A


async def test_resolve_branch_is_the_one_resolution_entry_point() -> None:
    directory = FakeBranchDirectory(_snapshot())
    router = _router(_service(branch_directory=directory))
    resolved = await router._resolve_branch(router.services[0], UNIT[:7])
    assert resolved.name == UNIT and resolved.is_landing_unit
    assert directory.touched == [UNIT]
