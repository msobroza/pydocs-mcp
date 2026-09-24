"""ToolRouter resolves the ``branch`` selector once per call and hands the
resolution — and the answering project's own probe — to the envelope
(spec §6.4, §6.7, §6.11; #311, O19)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from pydocs_mcp.application.branch_directory import BranchSnapshot
from pydocs_mcp.application.branch_resolution import BranchSelectorKind
from pydocs_mcp.application.freshness import EnvelopeInfo
from pydocs_mcp.application.mcp_errors import InvalidArgumentError, NotFoundError
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
    FakeSymbolSource,
    make_envelope,
    make_project,
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
    """Records the branch each get_why mode read (#313), mode by mode."""

    def __init__(self) -> None:
        self.branches: list[str | None] = []
        self.modes: list[str] = []

    def _read(self, mode: str, branch: str | None) -> tuple[str, tuple[()], dict[str, Any]]:
        self.modes.append(mode)
        self.branches.append(branch)
        return f"WHY {mode}", (), {}

    async def why_search(self, query: str, *, branch: str | None = None):
        return self._read("search", branch)

    async def why_targets(self, targets: list[str], *, query: str = "", branch: str | None = None):
        return self._read("targets", branch)

    async def why_dashboard(self, *, branch: str | None = None):
        return self._read("dashboard", branch)


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


async def test_the_router_hands_each_file_tool_the_branch_its_request_resolved_to() -> None:
    """#314: one resolution per request feeds both the file tool and meta."""
    files = FakeFileTools()
    directory = FakeBranchDirectory(_snapshot(live="main"))
    router = _router(_service(files=files, branch_directory=directory))
    await router.grep(BranchSelectedInput(GrepInput(pattern="x"), "feature/x"))
    await router.glob(GlobInput(pattern="*"))
    await router.read_file(BranchSelectedInput(ReadFileInput(file_path="a.py"), "main"))
    assert [(b.name, b.kind) for b in files.branches] == [  # type: ignore[attr-defined]
        ("feature/x", BranchSelectorKind.NAME),
        ("main", BranchSelectorKind.DEFAULT),
        ("main", BranchSelectorKind.NAME),
    ]


# ── #313: the symbol tools read the branch their request resolved to ──────

# The four tools that read a branch's tree, get_symbol's source depth included.
_TREE_TOOLS: tuple[tuple[str, Any], ...] = (
    ("get_symbol", SymbolInput(target="pkg.mod.X")),
    ("get_symbol", SymbolInput(target="pkg.mod.X", depth="source")),
    ("get_context", ContextInput(targets=["pkg.mod.X"])),
    ("get_references", ReferencesInput(target="pkg.mod.X")),
    ("get_why", WhyInput(query="why")),
)


@pytest.mark.parametrize(("method", "payload"), _TREE_TOOLS, ids=[m for m, _ in _TREE_TOOLS])
async def test_a_landing_unit_is_refused_by_the_tools_that_read_a_tree(
    method: str, payload: Any
) -> None:
    """ADR 0024 decision 5 / O17: a unit has no tree, and these tools carry no
    suggestion field — they raise instead of answering from the default."""
    router = _router(_service(branch_directory=FakeBranchDirectory(_snapshot())))
    with pytest.raises(InvalidArgumentError) as caught:
        await getattr(router, method)(BranchSelectedInput(payload, UNIT[:7]))
    assert str(caught.value) == (
        "'1234567' is a landing unit and has no tree; use search_codebase or grep with "
        "scope=diff, or name a branch"
    )


async def test_each_symbol_tool_reads_the_branch_the_request_resolved_to() -> None:
    """On a multi-branch bundle the default selector resolves the checkout
    (feature/x here), and every body reads it — the lookup and source
    services bound to it, get_why and the overview handed it."""
    svc = _service(branch_directory=FakeBranchDirectory(_snapshot()))
    router = _router(svc)
    await router.get_symbol(SymbolInput(target="pkg.mod.X"))
    await router.get_symbol(SymbolInput(target="pkg.mod.X", depth="source"))
    await router.get_why(WhyInput(query="why"))
    overview = await router.get_overview(OverviewInput())
    # One binding of the service set per get_symbol request (summary, source).
    assert svc.lookup.bound_branches == ["feature/x"] * 2  # type: ignore[attr-defined]
    assert svc.symbol_source.bound_branches == ["feature/x"] * 2  # type: ignore[attr-defined]
    assert svc.decisions.branches == ["feature/x"]  # type: ignore[attr-defined]
    assert svc.overview.branches == ["feature/x"]  # type: ignore[attr-defined]
    assert "# Overview — __project__ · branch feature/x" in overview.text


async def test_a_single_branch_bundle_binds_nothing_and_names_no_branch() -> None:
    """Spec R7: one branch row means every read keeps its served-default SQL
    and the card its bytes — unless the request names the branch."""
    rows = (_row("main", is_default=True),)
    svc = _service(branch_directory=FakeBranchDirectory(BranchSnapshot(rows, "main", "main", {})))
    router = _router(svc)
    await router.get_symbol(SymbolInput(target="pkg.mod.X"))
    await router.get_why(WhyInput(query="why"))
    plain = await router.get_overview(OverviewInput())
    named = await router.get_overview(BranchSelectedInput(OverviewInput(), "main"))
    assert svc.lookup.bound_branches == [] and svc.decisions.branches == [None]  # type: ignore[attr-defined]
    assert svc.overview.branches == [None, "main"]  # type: ignore[attr-defined]
    assert "· branch" not in plain.text and "· branch main" in named.text


async def test_every_get_why_mode_reads_the_resolved_branch() -> None:
    """#313 with #346: query, targets, both-set and dashboard modes each hand
    the decision service the branch the request resolved to."""
    svc = _service(branch_directory=FakeBranchDirectory(_snapshot()))
    router = _router(svc)
    target = ["pkg.mod.X"]
    for payload in (
        WhyInput(query="why"),
        WhyInput(targets=target),
        WhyInput(query="why", targets=target),
        WhyInput(),
    ):
        await router.get_why(payload)
    assert svc.decisions.modes == ["search", "targets", "targets", "dashboard"]  # type: ignore[attr-defined]
    assert svc.decisions.branches == ["feature/x"] * 4  # type: ignore[attr-defined]


async def test_a_landing_unit_overview_reads_and_names_the_unit_until_its_card() -> None:
    """P1 pins what get_overview does with a landing unit, which ADR 0024
    decision 5 lets it answer: the unit's rows (none yet) under its sha. The
    landing card (P2.4) replaces this on purpose."""
    svc = _service(branch_directory=FakeBranchDirectory(_snapshot()))
    response = await _router(svc).get_overview(BranchSelectedInput(OverviewInput(), UNIT[:7]))
    assert svc.overview.branches == [UNIT]  # type: ignore[attr-defined]
    assert f"# Overview — __project__ · branch {UNIT}" in response.text


async def test_a_union_lookup_resolves_only_the_bundles_it_visits() -> None:
    """#312's lazy pins hold for #313's recency walk: when the newest bundle
    answers in pass 1, no other bundle resolves a branch or touches one (a
    touch is the last_used_at the next index pass persists)."""
    beta_directory = FakeBranchDirectory(_snapshot())
    alpha = _service(
        "alpha",
        project=make_project("alpha", indexed_at=2.0),
        branch_directory=FakeBranchDirectory(_snapshot()),
    )
    beta = _service(
        "beta", project=make_project("beta", indexed_at=1.0), branch_directory=beta_directory
    )
    router = _router(alpha, beta)
    await router.get_symbol(SymbolInput(target="pkg.mod.X"))
    await router.get_symbol(SymbolInput(target="pkg.mod.X", depth="source"))
    await router.get_context(ContextInput(targets=["pkg.mod.X"]))
    assert (beta_directory.snapshots, beta_directory.touched) == (0, [])


async def test_a_union_lookup_resolves_each_bundle_it_visits_once() -> None:
    """A pass-1 miss on the newest bundle binds the next one; pass 2 (every
    bundle missed) reuses the bindings pass 1 made."""
    beta_directory = FakeBranchDirectory(_snapshot())
    alpha = _service(
        "alpha",
        project=make_project("alpha", indexed_at=2.0),
        symbol_source=FakeSymbolSource(known_targets=frozenset()),
        branch_directory=FakeBranchDirectory(_snapshot()),
    )
    beta = _service(
        "beta",
        project=make_project("beta", indexed_at=1.0),
        symbol_source=FakeSymbolSource(known_targets=frozenset({"pkg.mod.X"})),
        branch_directory=beta_directory,
    )
    router = _router(alpha, beta)
    await router.get_symbol(SymbolInput(target="pkg.mod.X", depth="source"))
    assert (beta_directory.snapshots, beta_directory.touched) == (1, ["feature/x"])
    with pytest.raises(NotFoundError):
        await router.get_symbol(SymbolInput(target="pkg.mod.Y", depth="source"))
    assert beta_directory.snapshots == 2
