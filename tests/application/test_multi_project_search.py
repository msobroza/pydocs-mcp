"""MultiProjectSearch / MultiProjectLookup: routing, union, dedup priority."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_errors import InvalidArgumentError, NotFoundError
from pydocs_mcp.application.mcp_inputs import LookupInput, SearchInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
    _merge_ranked,
)
from pydocs_mcp.application.null_services import NullDecisionService
from pydocs_mcp.application.overview_service import OverviewService
from pydocs_mcp.application.symbol_source import SymbolSourceService
from pydocs_mcp.application.target_resolution import (
    NullTargetResolver,
    TargetResolution,
    TargetRewrite,
)
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    Chunk,
    ChunkList,
    ModuleMember,
    ModuleMemberList,
    SearchResponse,
)
from pydocs_mcp.multirepo import LoadedProject
from pydocs_mcp.storage.index_metadata import IndexMetadata

from pydocs_mcp.retrieval.config import TargetResolutionConfig

from .._fakes import FakeTargetResolver, make_fake_uow_factory


def _project(name: str, indexed_at: float) -> LoadedProject:
    meta = IndexMetadata(
        project_name=name,
        project_root="",
        embedding_provider="fastembed",
        embedding_model="bge",
        embedding_dim=384,
        pipeline_hash="h",
        indexed_at=indexed_at,
    )
    return LoadedProject(name=name, db_path=Path(f"/x/{name}.db"), metadata=meta)


def _chunk(package: str, qname: str, relevance: float, text: str = "") -> Chunk:
    return Chunk(
        text=text or f"{package}:{qname}",
        relevance=relevance,
        metadata={"package": package, "qualified_name": qname, "title": qname},
    )


class _FakeDocs:
    def __init__(self, ranked: tuple[Chunk, ...], composite: str = "SINGLE") -> None:
        self._ranked = ChunkList(items=ranked)
        self._composite = composite

    async def ranked(self, query):
        return self._ranked

    async def search(self, query):
        item = Chunk(text=self._composite, metadata={"title": "c"})
        return SearchResponse(result=ChunkList(items=(item,)), query=query, duration_ms=0.0)


class _FakeApi:
    def __init__(self, ranked: tuple[ModuleMember, ...] = ()) -> None:
        self._ranked = ModuleMemberList(items=ranked)

    async def ranked(self, query):
        return self._ranked

    async def search(self, query):
        return SearchResponse(result=self._ranked, query=query, duration_ms=0.0)


class _FakeLookup:
    def __init__(self, answer: str | None, *, resolver: object | None = None) -> None:
        self._answer = answer  # None -> raises NotFoundError
        # Read by multi-project pass 2 (spec 2026-09-10 §2.5).
        self.target_resolver = resolver or NullTargetResolver()

    async def lookup(self, payload):
        if self._answer is None:
            raise NotFoundError(f"'{payload.target}' not indexed")
        return self._answer

    async def lookup_with_items(self, payload):
        # Routing tests care about text + routing only — empty rows.
        return await self.lookup(payload), (), {}

    async def lookup_exact(self, payload):
        return await self.lookup_with_items(payload)

    async def lookup_rewritten(self, payload, rewrite):
        return f"REWRITTEN {rewrite.canonical}", (), {}


class _StubTreeNavigator:
    """TreeNavigator slice for member-span resolution: (package, module) → tree."""

    def __init__(self, trees: dict[tuple[str, str], DocumentNode]) -> None:
        self._trees = trees

    async def get_tree(self, package: str, module: str) -> DocumentNode | None:
        return self._trees.get((package, module))


def _svc(
    project: LoadedProject,
    ranked=(),
    composite="SINGLE",
    lookup="",
    members=(),
    tree_navigator=None,
    resolver=None,
) -> ProjectServices:
    # symbol_source / decisions are unused by these routing/dedup tests, but the
    # ProjectServices contract now requires them (spec §D1) — wire the real
    # stateless SymbolSourceService (empty in-memory uow) + NullDecisionService.
    lookup_svc = _FakeLookup(lookup, resolver=resolver)
    if tree_navigator is not None:
        # Duck-typed LookupService slice: only ``tree_svc`` is read by the
        # member-span resolution (contract §3.2 best-effort spans).
        lookup_svc.tree_svc = tree_navigator
    return ProjectServices(
        project=project,
        docs=_FakeDocs(ranked, composite),
        api=_FakeApi(members),
        lookup=lookup_svc,
        symbol_source=SymbolSourceService(uow_factory=make_fake_uow_factory()),
        overview=OverviewService(uow_factory=make_fake_uow_factory(), scripts={}),
        decisions=NullDecisionService(),
    )


# ── _merge_ranked (the dedup priority rule) ──


def test_merge_dedups_same_dependency_most_recent_wins() -> None:
    old = _project("webapp", 100.0)
    new = _project("backend", 200.0)
    # Both depend on requests -> same (package, qualified_name); keep the newest db's.
    tagged = [
        (old, _chunk("requests", "requests.get", 0.9, text="OLD")),
        (new, _chunk("requests", "requests.get", 0.9, text="NEW")),
    ]
    merged = _merge_ranked(tagged, limit=10)
    assert len(merged) == 1 and merged[0].text == "NEW"


def test_merge_root_project_beats_dependency() -> None:
    root = _project("requests", 100.0)  # requests indexed as its OWN project
    dep = _project("webapp", 999.0)  # webapp depends on requests (newer, but a dep)
    tagged = [
        (dep, _chunk("requests", "requests.get", 0.9, text="DEP")),
        (root, _chunk(PROJECT_PACKAGE_NAME, "requests.get", 0.9, text="ROOT")),
    ]
    merged = _merge_ranked(tagged, limit=10)
    # Root wins despite the dependency copy being more recently indexed.
    assert len(merged) == 1 and merged[0].text == "ROOT"


def test_merge_ranks_survivors_by_score() -> None:
    p = _project("a", 1.0)
    q = _project("b", 1.0)
    tagged = [
        (p, _chunk("x", "x.low", 0.2, text="LOW")),
        (q, _chunk("y", "y.high", 0.8, text="HIGH")),
    ]
    merged = _merge_ranked(tagged, limit=10)
    assert [c.text for c in merged] == ["HIGH", "LOW"]


def test_merge_respects_limit() -> None:
    p = _project("a", 1.0)
    tagged = [(p, _chunk("x", f"x.{i}", float(i))) for i in range(5)]
    assert len(_merge_ranked(tagged, limit=3)) == 3


# ── MultiProjectSearch routing ──


@pytest.mark.asyncio
async def test_single_project_delegates() -> None:
    router = MultiProjectSearch(services=(_svc(_project("solo", 1.0), composite="SOLO"),))
    out = await router.search(SearchInput(query="x", kind="docs"))
    assert out == "SOLO"


@pytest.mark.asyncio
async def test_search_unknown_project_raises_typed_invalid_argument() -> None:
    # A typo'd project= selector must surface as a typed client-input error
    # (InvalidArgumentError), not the raw KeyError select_project raises nor
    # the ServiceUnavailableError server._run_tool's generic except-Exception
    # arm would produce if the KeyError escaped uncaught. Drives the unknown
    # name through MultiProjectSearch itself (not just the full ToolRouter),
    # pinning the _select_service seam directly.
    router = MultiProjectSearch(
        services=(
            _svc(_project("frontend", 1.0), composite="FRONT"),
            _svc(_project("backend", 2.0), composite="BACK"),
        )
    )
    with pytest.raises(InvalidArgumentError) as exc_info:
        await router.search(SearchInput(query="x", kind="docs", project="nope"))
    message = str(exc_info.value)
    assert "nope" in message
    assert "frontend" in message and "backend" in message


@pytest.mark.asyncio
async def test_project_scope_routes_to_one() -> None:
    router = MultiProjectSearch(
        services=(
            _svc(_project("frontend", 1.0), composite="FRONT"),
            _svc(_project("backend", 2.0), composite="BACK"),
        )
    )
    out = await router.search(SearchInput(query="x", kind="docs", project="backend"))
    assert out == "BACK"


@pytest.mark.asyncio
async def test_union_dedups_and_ranks_across_projects() -> None:
    a = _svc(_project("a", 1.0), ranked=(_chunk("shared", "shared.f", 0.5, text="A"),))
    b = _svc(
        _project("b", 2.0),
        ranked=(
            _chunk("shared", "shared.f", 0.5, text="B"),  # dup of a's -> newer b wins
            _chunk("bpkg", "bpkg.g", 0.9, text="BHIGH"),
        ),
    )
    router = MultiProjectSearch(services=(a, b))
    out = await router.search(SearchInput(query="x", kind="docs"))
    assert "BHIGH" in out and "B" in out and "A" not in out.split("BHIGH")[0]


# ── _search_body items[] (contract §3.2, Task 5) ──


def _member(id_: int, package: str, module: str, name: str, relevance: float) -> ModuleMember:
    return ModuleMember(
        id=id_,
        relevance=relevance,
        metadata={"package": package, "module": module, "name": name, "kind": "class"},
    )


def _routing_tree() -> DocumentNode:
    cls = DocumentNode(
        node_id="fastapi.routing.APIRouter",
        qualified_name="fastapi.routing.APIRouter",
        title="class APIRouter",
        kind=NodeKind.CLASS,
        source_path="fastapi/routing.py",
        start_line=10,
        end_line=40,
        text="class APIRouter: ...",
        content_hash="h-class",
    )
    return DocumentNode(
        node_id="fastapi.routing",
        qualified_name="fastapi.routing",
        title="fastapi.routing",
        kind=NodeKind.MODULE,
        source_path="fastapi/routing.py",
        start_line=1,
        end_line=50,
        text="",
        content_hash="h-mod",
        children=(cls,),
    )


@pytest.mark.asyncio
async def test_union_search_body_emits_chunk_items_from_merged_rows() -> None:
    a = _svc(_project("a", 1.0), ranked=(_chunk("apkg", "apkg.f", 0.2),))
    b = _svc(_project("b", 2.0), ranked=(_chunk("bpkg", "bpkg.g", 0.9),))
    router = MultiProjectSearch(services=(a, b))
    _body, items, extras = await router._search_body(SearchInput(query="x", kind="docs"))
    assert extras == {}
    assert [(i["kind"], i["qualified_name"], i["score"]) for i in items] == [
        ("chunk", "bpkg.g", 0.9),
        ("chunk", "apkg.f", 0.2),
    ]


@pytest.mark.asyncio
async def test_union_search_body_resolves_member_spans_per_owning_project() -> None:
    # Project a can resolve its member's span through its OWN tree navigator;
    # project b has no trees -> its row degrades to null path/span (§3.2).
    a = _svc(
        _project("a", 1.0),
        members=(_member(7, "fastapi", "fastapi.routing", "APIRouter", 0.9),),
        tree_navigator=_StubTreeNavigator({("fastapi", "fastapi.routing"): _routing_tree()}),
    )
    b = _svc(_project("b", 2.0), members=(_member(8, "bpkg", "bpkg.mod", "G", 0.3),))
    router = MultiProjectSearch(services=(a, b))
    _body, items, _extras = await router._search_body(SearchInput(query="x", kind="api"))
    assert items == (
        {
            "kind": "member",
            "id": "7",
            "qualified_name": "fastapi.routing.APIRouter",
            "package": "fastapi",
            "path": "fastapi/routing.py",
            "start_line": 10,
            "end_line": 40,
            "score": 0.9,
        },
        {
            "kind": "member",
            "id": "8",
            "qualified_name": "bpkg.mod.G",
            "package": "bpkg",
            "path": None,
            "start_line": None,
            "end_line": None,
            "score": 0.3,
        },
    )


# ── MultiProjectLookup routing ──


@pytest.mark.asyncio
async def test_lookup_project_scope_routes() -> None:
    router = MultiProjectLookup(
        services=(
            _svc(_project("a", 1.0), lookup="ANSWER_A"),
            _svc(_project("b", 2.0), lookup="ANSWER_B"),
        )
    )
    out = await router.lookup(LookupInput(target="x.y", project="a"))
    assert out == "ANSWER_A"


@pytest.mark.asyncio
async def test_lookup_unknown_project_raises_typed_invalid_argument() -> None:
    # Same seam as the search-side test above, for MultiProjectLookup's
    # project= routing path (_select_service is shared by both routers).
    router = MultiProjectLookup(
        services=(
            _svc(_project("a", 1.0), lookup="ANSWER_A"),
            _svc(_project("b", 2.0), lookup="ANSWER_B"),
        )
    )
    with pytest.raises(InvalidArgumentError) as exc_info:
        await router.lookup(LookupInput(target="x.y", project="nope"))
    message = str(exc_info.value)
    assert "nope" in message
    assert "a" in message and "b" in message


@pytest.mark.asyncio
async def test_lookup_resolves_recency_first_skipping_notfound() -> None:
    # 'a' (older) has it; 'b' (newer) does not -> recency-first tries b, NotFound, then a.
    a = _svc(_project("a", 1.0), lookup="FOUND_IN_A")
    b = _svc(_project("b", 2.0), lookup=None)  # raises NotFoundError
    router = MultiProjectLookup(services=(a, b))
    out = await router.lookup(LookupInput(target="x.y"))
    assert out == "FOUND_IN_A"


@pytest.mark.asyncio
async def test_lookup_all_notfound_raises() -> None:
    router = MultiProjectLookup(
        services=(_svc(_project("a", 1.0), lookup=None), _svc(_project("b", 2.0), lookup=None))
    )
    with pytest.raises(NotFoundError):
        await router.lookup(LookupInput(target="x.y"))


@pytest.mark.asyncio
async def test_lookup_empty_target_unions_listings() -> None:
    router = MultiProjectLookup(
        services=(
            _svc(_project("a", 1.0), lookup="pkgs-a"),
            _svc(_project("b", 2.0), lookup="pkgs-b"),
        )
    )
    out = await router.lookup(LookupInput(target=""))
    assert "## Project: a" in out and "pkgs-a" in out and "## Project: b" in out


# ── pass 2: workspace target fallback (spec 2026-09-10 §2.5, AC13/AC14) ──

_BASE_MISS = "'Cls' not found in any loaded project. [[next:search:Cls]]"


def _bare_rewrite(canonical: str) -> TargetResolution:
    module, leaf = canonical.rsplit(".", 1)
    rewrite = TargetRewrite("unique_bare_name", canonical, module, (leaf,))
    return TargetResolution(rewrite=rewrite, exact_leaf_count=1)


def _pass2_router(
    old: TargetResolution, new: TargetResolution, *, old_answer: str | None = None, **kw
) -> tuple[MultiProjectLookup, FakeTargetResolver, FakeTargetResolver]:
    """ "old" (indexed_at 1) and "new" (indexed_at 2); each resolver answers "Cls"."""
    old_res = FakeTargetResolver(resolution_by_target={"Cls": old})
    new_res = FakeTargetResolver(resolution_by_target={"Cls": new})
    services = (
        _svc(_project("old", 1.0), lookup=old_answer, resolver=old_res),
        _svc(_project("new", 2.0), lookup=None, resolver=new_res),
    )
    return MultiProjectLookup(services=services, **kw), old_res, new_res


def _pass2_error(router: MultiProjectLookup) -> str:
    with pytest.raises(NotFoundError) as info:
        asyncio.run(router._lookup_body(LookupInput(target="Cls")))
    return str(info.value)


def test_pass2_exact_hit_in_older_project_beats_rewrite_in_newer() -> None:
    router, _old_res, new_res = _pass2_router(
        TargetResolution(), _bare_rewrite("n.m.Cls"), old_answer="OLD"
    )
    body, _items, _extras = asyncio.run(router._lookup_body(LookupInput(target="Cls")))
    assert body == "OLD"
    assert new_res.calls == []  # pass 1 won — no resolver consulted


def test_pass2_rewrites_in_two_projects_raise_the_ambiguity_error() -> None:
    router, _, _ = _pass2_router(_bare_rewrite("o.m.Cls"), _bare_rewrite("n.m.Cls"))
    assert _pass2_error(router) == (
        f"{_BASE_MISS} Ambiguous name 'Cls' matches 2 indexed symbols across projects: "
        "n.m.Cls (project new), o.m.Cls (project old)."
    )


def test_pass2_unique_in_one_project_but_ambiguous_in_another_does_not_resolve() -> None:
    ambiguous = TargetResolution(
        candidates=("o.a.Cls", "o.b.Cls"), candidate_total=2, exact_leaf_count=2, ambiguous=True
    )
    router, _, _ = _pass2_router(ambiguous, _bare_rewrite("n.m.Cls"))
    assert _pass2_error(router) == (
        f"{_BASE_MISS} Ambiguous name 'Cls' matches 3 indexed symbols across projects: "
        "n.m.Cls (project new), o.a.Cls (project old), o.b.Cls (project old)."
    )


def test_pass2_all_miss_keeps_search_pointer_and_merges_candidates_by_recency() -> None:
    router, _, _ = _pass2_router(
        TargetResolution(candidates=("o.m.Clx",), candidate_total=1),
        TargetResolution(candidates=("n.m.Clz",), candidate_total=1),
    )
    assert _pass2_error(router) == (
        f"{_BASE_MISS} Closest indexed names: n.m.Clz (project new), o.m.Clx (project old)."
    )


def test_pass2_merged_candidates_respect_the_yaml_cap() -> None:
    router, _, _ = _pass2_router(
        TargetResolution(candidates=("o.m.Clx",), candidate_total=1),
        TargetResolution(candidates=("n.m.Clz",), candidate_total=1),
        target_resolution=TargetResolutionConfig(max_candidates=1),
    )
    assert _pass2_error(router) == (
        f"{_BASE_MISS} Closest indexed names: n.m.Clz (project new) (+1 more)."
    )


def test_pass2_miss_candidates_off_keeps_todays_message() -> None:
    router, _, _ = _pass2_router(
        _bare_rewrite("o.m.Cls"),
        _bare_rewrite("n.m.Cls"),
        target_resolution=TargetResolutionConfig(miss_candidates=False),
    )
    assert _pass2_error(router) == _BASE_MISS


def test_pass2_resolved_rewrite_runs_once_and_logs_the_project(
    caplog: pytest.LogCaptureFixture,
) -> None:
    router, _, _ = _pass2_router(TargetResolution(), _bare_rewrite("n.m.Cls"))
    with caplog.at_level(logging.INFO, logger="pydocs_mcp.application.target_resolution"):
        body, _items, _extras = asyncio.run(router._lookup_body(LookupInput(target="Cls")))
    assert body == "REWRITTEN n.m.Cls"
    events = [json.loads(r.getMessage()) for r in caplog.records]
    assert events == [
        {
            "event": "target_fallback_resolved",
            "entry": "lookup",
            "rule": "unique_bare_name",
            "target": "Cls",
            "resolved": "n.m.Cls",
            "project": "new",
        }
    ]
    assert list(events[0]) == ["event", "entry", "rule", "target", "resolved", "project"]


def test_pass2_miss_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    router, _, _ = _pass2_router(TargetResolution(), TargetResolution())
    with caplog.at_level(logging.INFO, logger="pydocs_mcp.application.target_resolution"):
        assert _pass2_error(router) == _BASE_MISS
    assert caplog.records == []
