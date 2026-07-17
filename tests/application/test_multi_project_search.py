"""MultiProjectSearch / MultiProjectLookup: routing, union, dedup priority."""

from __future__ import annotations

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

from .._fakes import make_fake_uow_factory


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
    def __init__(self, answer: str | None) -> None:
        self._answer = answer  # None -> raises NotFoundError

    async def lookup(self, payload):
        if self._answer is None:
            raise NotFoundError(f"'{payload.target}' not indexed")
        return self._answer


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
) -> ProjectServices:
    # symbol_source / decisions are unused by these routing/dedup tests, but the
    # ProjectServices contract now requires them (spec §D1) — wire the real
    # stateless SymbolSourceService (empty in-memory uow) + NullDecisionService.
    lookup_svc = _FakeLookup(lookup)
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
