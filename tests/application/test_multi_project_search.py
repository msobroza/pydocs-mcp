"""MultiProjectSearch / MultiProjectLookup: routing, union, dedup priority."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.mcp_inputs import LookupInput, SearchInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
    _merge_ranked,
)
from pydocs_mcp.application.null_services import NullDecisionService
from pydocs_mcp.application.symbol_source import SymbolSourceService
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    Chunk,
    ChunkList,
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
    def __init__(self) -> None:
        self._ranked = ModuleMemberList(items=())

    async def ranked(self, query):
        return self._ranked

    async def search(self, query):
        return SearchResponse(result=ModuleMemberList(items=()), query=query, duration_ms=0.0)


class _FakeLookup:
    def __init__(self, answer: str | None) -> None:
        self._answer = answer  # None -> raises NotFoundError

    async def lookup(self, payload):
        if self._answer is None:
            raise NotFoundError(f"'{payload.target}' not indexed")
        return self._answer


def _svc(project: LoadedProject, ranked=(), composite="SINGLE", lookup="") -> ProjectServices:
    # symbol_source / decisions are unused by these routing/dedup tests, but the
    # ProjectServices contract now requires them (spec §D1) — wire the real
    # stateless SymbolSourceService (empty in-memory uow) + NullDecisionService.
    return ProjectServices(
        project=project,
        docs=_FakeDocs(ranked, composite),
        api=_FakeApi(),
        lookup=_FakeLookup(lookup),
        symbol_source=SymbolSourceService(uow_factory=make_fake_uow_factory()),
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
