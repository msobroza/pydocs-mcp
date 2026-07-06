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
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.application.multi_project_search import ProjectServices
from pydocs_mcp.application.null_services import NullDecisionService
from pydocs_mcp.application.overview_service import (
    CommunityEntry,
    EntryPoint,
    ModuleEntry,
    OverviewCard,
)
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

    async def lookup(self, payload: LookupInput) -> str:
        if payload.show == "impact":
            return f"Impact of {payload.target}\n\nimpact body"
        if not payload.target:
            return "## Packages\n- pkg"
        return f"## {payload.target}\n\nsummary body"

    async def context_nodes(self, target: str) -> tuple[str, tuple[str, ...]]:
        # Trivial one-node closure keyed by target — enough for the router's
        # proportional split (all sizes equal → even shares).
        return target, (f"{target}.dep0",)

    def render_context_card(self, target: str, nodes: tuple[str, ...], *, token_budget: int) -> str:
        return f"# Context for {target}\n\nctx body ({len(nodes)} nodes)"


class FakeSymbolSource:
    """Verbatim-source stand-in for ToolRouter's ``depth='source'`` route."""

    async def source_for(self, target: str) -> str:
        return f"# Source — `{target}`\n\n```python\ndef f():\n    return 1\n```\n"


class FakeOverview:
    """A build() that returns a fixed OverviewCard so ToolRouter's get_overview
    routing (svc.overview.build → format_overview_card) is observable without
    the real OverviewService + uow."""

    async def build(self, package: str = "") -> OverviewCard:
        return OverviewCard(
            package=package or "__project__",
            package_count=1,
            module_count=1,
            symbol_count=2,
            doc_coverage=0.5,
            modules=(ModuleEntry("pkg.mod", "A module.", 0.5),),
            entry_points=(EntryPoint("pkg.__main__", "module"),),
            communities=(CommunityEntry("pkg", 2, 0.5, "pkg.mod"),),
            dependency_profile=(("numpy", 1),),
            node_scores_available=True,
        )


def make_project() -> LoadedProject:
    meta = IndexMetadata(
        project_name="solo",
        project_root="",
        embedding_provider="fastembed",
        embedding_model="bge",
        embedding_dim=384,
        pipeline_hash="h",
        indexed_at=0.0,
    )
    return LoadedProject(name="solo", db_path=Path("/x/solo.db"), metadata=meta)


def make_services() -> tuple[ProjectServices, ...]:
    return (
        ProjectServices(
            project=make_project(),
            docs=FakeDocs(),
            api=FakeApi(),
            lookup=FakeLookup(),
            symbol_source=FakeSymbolSource(),
            overview=FakeOverview(),
            decisions=NullDecisionService(),
        ),
    )


def make_envelope(surface: str = "mcp") -> ResponseEnvelope:
    return ResponseEnvelope(probe=StaticProbe(), surface=surface, pointers_enabled=True)
