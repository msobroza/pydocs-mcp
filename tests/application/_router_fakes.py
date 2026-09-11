"""Shared router-test fakes — one fake ``ProjectServices`` set + a static
envelope probe, reused by ``test_router_envelope_wiring.py`` and
``test_tool_router.py`` so the envelope/routing conventions are exercised
against one fixture (spec §D1/§D3/§D4).
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.formatting import pointer_token
from pydocs_mcp.application.freshness import EnvelopeInfo
from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.application.multi_project_search import ProjectServices
from pydocs_mcp.application.null_services import NullDecisionService
from pydocs_mcp.application.overview_service import (
    CommunityEntry,
    EntryPoint,
    ModuleEntry,
    OverviewCard,
)
from pydocs_mcp.application.target_resolution import NullTargetResolver, TargetRewrite
from pydocs_mcp.models import ChunkList, ModuleMemberList, SearchResponse
from pydocs_mcp.multirepo import LoadedProject
from pydocs_mcp.storage.index_metadata import IndexMetadata

SHA = "8e2110e" + "0" * 33


class StaticProbe:
    async def envelope_info(self) -> EnvelopeInfo:
        return EnvelopeInfo(
            indexed_commit=SHA,
            live_commit=SHA,
            age_days=0,
            package_count=1,
            stale=False,
        )


class FakeDocs:
    """A docs search whose composite hit carries a lookup pointer token for
    ``pkg.mod.X`` — mirroring what the real formatting pipeline emits."""

    async def search(self, query):
        from pydocs_mcp.models import Chunk

        text = f"## X\nbody\n{pointer_token('lookup', 'pkg.mod.X')}\n"
        item = Chunk(text=text, metadata={"title": "X", "qualified_name": "pkg.mod.X"})
        return SearchResponse(result=ChunkList(items=(item,)), query=query, duration_ms=0.0)

    async def ranked(self, query):
        return ChunkList(items=())


class FakeApi:
    async def search(self, query):
        return SearchResponse(result=ModuleMemberList(items=()), query=query, duration_ms=0.0)

    async def ranked(self, query):
        return ModuleMemberList(items=())


class FakeLookup:
    """A lookup body that echoes the requested ``show`` mode so ToolRouter's
    depth/direction → ``show`` mapping is observable.

    Empty target = "list packages"; ``show="impact"`` renders an impact
    heading — enough for the router tests to assert routing without pulling in
    the real LookupService.

    ``get_context`` no longer routes through ``lookup(show="context")``: it uses
    the two-phase ``context_nodes`` (resolve) + ``render_context_card`` (render)
    split. This fake mirrors that seam — ``context_nodes`` returns a one-node
    closure per target and ``render_context_card`` emits the ``# Context for``
    heading — so the router's "one card per target" routing stays observable
    without the real LookupService + reference graph.
    """

    # Read by ``get_context`` phase 2 as the shared budget to split; the fake
    # closures are one node each so the split is even and the value is inert.
    context_token_budget = 2048

    def __init__(self, target_resolver: object | None = None) -> None:
        # Read by ToolRouter's depth="source" fallback and multi-project pass 2.
        self.target_resolver = target_resolver or NullTargetResolver()

    async def lookup(self, payload: LookupInput) -> str:
        if payload.show == "impact":
            return f"Impact of {payload.target}\n\nimpact body"
        if not payload.target:
            return "## Packages\n- pkg"
        return f"## {payload.target}\n\nsummary body"

    async def lookup_with_items(
        self, payload: LookupInput
    ) -> tuple[str, tuple[dict[str, object], ...], dict[str, object]]:
        # Text-only fake bodies — the §3.3 rows are covered by the real
        # LookupService tests; the router only threads the triple through.
        return await self.lookup(payload), (), {}

    async def lookup_exact(
        self, payload: LookupInput
    ) -> tuple[str, tuple[dict[str, object], ...], dict[str, object]]:
        return await self.lookup_with_items(payload)

    async def lookup_rewritten(
        self, payload: LookupInput, rewrite: TargetRewrite
    ) -> tuple[str, tuple[dict[str, object], ...], dict[str, object]]:
        return await self.lookup_with_items(
            payload.model_copy(update={"target": rewrite.canonical})
        )

    async def context_nodes_exact(
        self, target: str
    ) -> tuple[str, tuple[str, ...], dict[str, object]]:
        return await self.context_nodes(target)

    async def context_nodes_rewritten(
        self, rewrite: TargetRewrite
    ) -> tuple[str, tuple[str, ...], dict[str, object]]:
        return await self.context_nodes(rewrite.canonical)

    async def context_nodes(self, target: str) -> tuple[str, tuple[str, ...], dict[str, object]]:
        # Trivial one-node closure keyed by target — enough for the router's
        # proportional split (all sizes equal → even shares). The third element
        # mirrors the real seam's §3.4 focus row.
        focus_row: dict[str, object] = {
            "qualified_name": target,
            "kind": "class",
            "path": None,
            "start_line": None,
            "end_line": None,
        }
        return target, (f"{target}.dep0",), focus_row

    def render_context_card(self, target: str, nodes: tuple[str, ...], *, token_budget: int) -> str:
        return f"# Context for {target}\n\nctx body ({len(nodes)} nodes)"


class FakeSymbolSource:
    """Verbatim-source stand-in for ToolRouter's ``depth='source'`` route.

    ``known_targets=None`` (default) resolves ANY target — the single-project
    router tests don't care which symbol. Pass an explicit set to model a
    real per-project ``SymbolSourceService``, which raises ``NotFoundError``
    for a target not indexed in ITS OWN db (mirrors the real service's
    ``uow.chunks.list`` miss) — needed to reproduce the multi-project
    recency-skip gap where only one loaded project holds the target.
    """

    def __init__(self, known_targets: frozenset[str] | None = None) -> None:
        self._known_targets = known_targets
        # (target, package) per source_with_items call — pins the P2 package pin.
        self.calls: list[tuple[str, str | None]] = []

    async def source_for(self, target: str) -> str:
        if self._known_targets is not None and target not in self._known_targets:
            raise NotFoundError(
                f"'{target}' has no indexed source. {pointer_token('search', target)}"
            )
        return f"# Source — `{target}`\n\n```python\ndef f():\n    return 1\n```\n"

    async def source_with_items(
        self, target: str, *, package: str | None = None
    ) -> tuple[str, tuple[dict[str, object], ...], dict[str, object]]:
        # Mirrors the real service's Task-6 shape: one §3.3 row for the span.
        self.calls.append((target, package))
        text = await self.source_for(target)
        row: dict[str, object] = {
            "node_id": target,
            "kind": "",
            "qualified_name": target,
            "path": None,
            "start_line": None,
            "end_line": None,
        }
        return text, (row,), {}


class FakeFileTools:
    """A ``FileToolsService`` stand-in whose bodies echo which filesystem tool
    ran (and, via ``marker``, WHICH project's service answered) so ToolRouter's
    grep/glob/read_file routing + §3.7-3.9 items[] threading is observable
    without a real source tree."""

    def __init__(self, marker: str = "solo") -> None:
        self.marker = marker
        self.calls: list[tuple[str, object]] = []

    async def grep(
        self, payload: object
    ) -> tuple[str, tuple[dict[str, object], ...], dict[str, object]]:
        self.calls.append(("grep", payload))
        row: dict[str, object] = {"path": "a.py", "start_line": 1, "end_line": 1, "text": "x"}
        return f"GREP-BODY {self.marker}", (row,), {}

    async def glob(
        self, payload: object
    ) -> tuple[str, tuple[dict[str, object], ...], dict[str, object]]:
        self.calls.append(("glob", payload))
        return f"GLOB-BODY {self.marker}", ({"path": "a.py", "mtime": 1.0},), {}

    async def read_file(
        self, payload: object
    ) -> tuple[str, tuple[dict[str, object], ...], dict[str, object]]:
        self.calls.append(("read_file", payload))
        row: dict[str, object] = {"path": "a.py", "start_line": 1, "end_line": 2}
        return f"READ-BODY {self.marker}", (row,), {}


class FakeOverview:
    """A build() that returns a fixed OverviewCard so ToolRouter's get_overview
    routing (svc.overview.build → format_overview_card) is observable without
    the real OverviewService + uow. ``package_count`` mirrors the light census
    read the workspace card gathers per loaded project."""

    def __init__(self, package_count: int = 1) -> None:
        self._package_count = package_count

    async def package_count(self) -> int:
        return self._package_count

    async def build(self, package: str = "") -> OverviewCard:
        return OverviewCard(
            package=package or "__project__",
            package_count=self._package_count,
            module_count=1,
            symbol_count=2,
            doc_coverage=0.5,
            modules=(ModuleEntry("pkg.mod", "A module.", 0.5),),
            entry_points=(EntryPoint("pkg.__main__", "module"),),
            communities=(CommunityEntry("pkg", 2, 0.5, "pkg.mod"),),
            dependency_profile=(("numpy", 1),),
            node_scores_available=True,
        )


def make_project(
    name: str = "solo", indexed_at: float = 0.0, *, loadable_grammars: str = ""
) -> LoadedProject:
    meta = IndexMetadata(
        project_name=name,
        project_root="",
        embedding_provider="fastembed",
        embedding_model="bge",
        embedding_dim=384,
        pipeline_hash="h",
        indexed_at=indexed_at,
        loadable_grammars=loadable_grammars,
    )
    return LoadedProject(name=name, db_path=Path(f"/x/{name}.db"), metadata=meta)


def make_service(
    name: str = "solo",
    *,
    package_count: int = 1,
    indexed_at: float = 0.0,
    symbol_source: object | None = None,
    files: object | None = None,
    target_resolver: object | None = None,
    loadable_grammars: str = "",
) -> ProjectServices:
    """One fake project's service set — parametrized so multi-repo router tests
    can load several distinguishable projects (workspace-card scenarios).

    ``symbol_source`` lets a caller inject a project-scoped ``FakeSymbolSource``
    (e.g. ``FakeSymbolSource(known_targets=frozenset({...}))``) to model a
    target that is indexed in only ONE of several loaded projects. ``files``
    injects a per-project filesystem-tools stand-in (``FakeFileTools`` or a
    real ``FileToolsService``); omitted, ``ProjectServices``' read-only-bundle
    default applies. ``target_resolver`` injects a ``FakeTargetResolver`` into
    the project's ``FakeLookup`` (the seam ToolRouter's source fallback reads).
    ``loadable_grammars`` is the bundle's index-time grammar stamp (``""`` =
    unstamped, the pre-stamp bundle shape).
    """
    extra = {} if files is None else {"files": files}
    return ProjectServices(
        project=make_project(name, indexed_at, loadable_grammars=loadable_grammars),
        docs=FakeDocs(),
        api=FakeApi(),
        lookup=FakeLookup(target_resolver),
        symbol_source=symbol_source if symbol_source is not None else FakeSymbolSource(),
        overview=FakeOverview(package_count),
        decisions=NullDecisionService(),
        **extra,
    )


def make_services() -> tuple[ProjectServices, ...]:
    return (make_service(),)


def make_envelope(surface: str = "mcp") -> ResponseEnvelope:
    return ResponseEnvelope(probe=StaticProbe(), surface=surface, pointers_enabled=True)
