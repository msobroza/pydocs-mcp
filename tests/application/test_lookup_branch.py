"""Read-side services answer from the selected branch and never mix branches (#313).

A named branch reads that branch plus the dependency tier (``''``) everywhere.
``None`` differs by store: the branch-keyed tiers (trees, members, edges,
scores, decisions) read the served default branch plus ``''`` (#307), while a
chunk read stays unpinned (#312) — exact only on a one-branch bundle, the only
place the router passes ``None``. Every service here runs over the in-memory
fakes; the real SQLite path is covered end to end by
``tests/integration/test_lookup_on_branch.py``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from pydocs_mcp.application.decision_service import DecisionService
from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.application.overview_service import OverviewService
from pydocs_mcp.application.package_lookup import PackageLookup
from pydocs_mcp.application.reference_service import ReferenceService
from pydocs_mcp.application.symbol_source import SymbolSourceService
from pydocs_mcp.application.target_resolution import ProjectTargetResolver
from pydocs_mcp.application.tree_service import TreeService
from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchIndexSource,
    Chunk,
    ChunkList,
    ModuleMember,
    Package,
    PackageOrigin,
)
from pydocs_mcp.retrieval.config import TargetResolutionConfig
from pydocs_mcp.storage.branch_records import BranchRecord, ChunkMembership
from pydocs_mcp.storage.decision_record import DecisionRecord
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import make_fake_uow_factory

MAIN, FEATURE = "main", "feature/x"


def _tree(module: str, start: int, *extra: str) -> DocumentNode:
    """``module`` with ``f`` (and ``extra``) at lines ``start``..``start + 1``:
    the span is what a symbol card renders, so it tells the branches apart."""
    text = f"lines {start}"
    fns = tuple(
        DocumentNode(
            f"{module}.{name}",
            f"{module}.{name}",
            name,
            NodeKind.FUNCTION,
            f"{module.replace('.', '/')}.py",
            start,
            start + 1,
            text,
            "h" + text + name,
        )
        for name in ("f", *extra)
    )
    return DocumentNode(
        module, module, module, NodeKind.MODULE, "pkg/a.py", 1, 9, text, "m" + text, children=fns
    )


def _branch(name: str, *, default: bool = False) -> BranchRecord:
    return BranchRecord(name, "a" * 40, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0, default)


async def _two_branch_factory():
    """``pkg.a`` on both branches; ``pkg.a.only_here`` on feature/x only; main
    is the served default."""
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.branches.upsert_branch(_branch(MAIN, default=True))
        await uow.branches.upsert_branch(_branch(FEATURE))
        await uow.trees.save_many([_tree("pkg.a", 1)], package=PROJECT_PACKAGE_NAME, branch=MAIN)
        await uow.trees.save_many(
            [_tree("pkg.a", 5, "only_here")],
            package=PROJECT_PACKAGE_NAME,
            branch=FEATURE,
        )
        await uow.commit()
    return factory


def _lookup(factory) -> LookupService:
    return LookupService(
        package_lookup=PackageLookup(uow_factory=factory),
        tree_svc=TreeService(uow_factory=factory),
        ref_svc=ReferenceService(uow_factory=factory),
    )


async def test_symbol_lookup_reads_the_selected_branch_tree() -> None:
    lookup = _lookup(await _two_branch_factory())
    body_main = await lookup.lookup_with_items(LookupInput(target="pkg.a.f"), branch=MAIN)
    body_feature = await lookup.lookup_with_items(LookupInput(target="pkg.a.f"), branch=FEATURE)
    assert "pkg/a.py:1-2" in body_main[0] and "pkg/a.py:5-6" in body_feature[0]


async def test_a_symbol_only_on_a_branch_resolves_there_and_is_unknown_elsewhere() -> None:
    lookup = _lookup(await _two_branch_factory())
    only_here = LookupInput(target="pkg.a.only_here")
    text, _items, _extras = await lookup.lookup_with_items(only_here, branch=FEATURE)
    assert "only_here" in text
    for branch in (MAIN, None):
        with pytest.raises(NotFoundError):
            await lookup.lookup_with_items(only_here, branch=branch)


async def test_no_branch_reads_the_served_default_branch() -> None:
    lookup = _lookup(await _two_branch_factory())
    text, _items, _extras = await lookup.lookup_with_items(LookupInput(target="pkg.a.f"))
    assert "pkg/a.py:1-2" in text


async def test_a_bound_lookup_binds_every_collaborator() -> None:
    lookup = _lookup(await _two_branch_factory())
    bound = lookup.on_branch(FEATURE)
    assert bound.tree_svc.branch == FEATURE and bound.ref_svc.branch == FEATURE
    assert bound.package_lookup.branch == FEATURE
    assert lookup.on_branch(None) is lookup


def _call_edge(caller: str, callee: str) -> NodeReference:
    return NodeReference(
        PROJECT_PACKAGE_NAME, caller, callee.rsplit(".", 1)[1], callee, ReferenceKind.CALLS
    )


async def test_callers_and_impact_are_branch_facts() -> None:
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.branches.upsert_branch(_branch(MAIN, default=True))
        await uow.references.save_many(
            [_call_edge("pkg.a.f", "pkg.b.g")], package=PROJECT_PACKAGE_NAME, branch=MAIN
        )
        await uow.references.save_many(
            [_call_edge("pkg.a.h", "pkg.b.g"), _call_edge("pkg.c.k", "pkg.a.h")],
            package=PROJECT_PACKAGE_NAME,
            branch=FEATURE,
        )
        await uow.commit()
    svc = ReferenceService(uow_factory=factory)
    on_main, on_feature = svc.on_branch(MAIN), svc.on_branch(FEATURE)
    assert {r.from_node_id for r in await on_main.callers(PROJECT_PACKAGE_NAME, "pkg.b.g")} == {
        "pkg.a.f"
    }
    assert {r.from_node_id for r in await on_feature.callers(PROJECT_PACKAGE_NAME, "pkg.b.g")} == {
        "pkg.a.h"
    }
    impact = await on_feature.impact(PROJECT_PACKAGE_NAME, "pkg.b.g", max_depth=3, limit=10)
    assert [n.qualified_name for n in impact] == ["pkg.a.h", "pkg.c.k"]
    served = await svc.impact(PROJECT_PACKAGE_NAME, "pkg.b.g", max_depth=3, limit=10)
    assert [n.qualified_name for n in served] == ["pkg.a.f"]


def _member(name: str, branch: str, package: str = PROJECT_PACKAGE_NAME) -> ModuleMember:
    metadata = {"package": package, "module": "pkg.a", "name": name, "kind": "function"}
    return ModuleMember(metadata={**metadata, "branch": branch, "docstring": "d"})


async def test_the_overview_counts_the_branch_members_and_names_the_branch() -> None:
    factory = await _two_branch_factory()
    async with factory() as uow:
        await uow.module_members.upsert_many(
            [
                _member("f", MAIN),
                _member("f", FEATURE),
                _member("only_here", FEATURE),
            ]
        )
        await uow.commit()
    overview = OverviewService(uow_factory=factory, scripts={})
    served, on_feature = await overview.build(), await overview.build(branch=FEATURE)
    assert (served.symbol_count, served.branch) == (1, "")
    assert (on_feature.symbol_count, on_feature.branch) == (2, FEATURE)
    assert (await overview.build(branch=MAIN)).branch == MAIN


async def test_the_package_doc_lists_only_the_read_branch_members() -> None:
    factory = await _two_branch_factory()
    async with factory() as uow:
        await uow.packages.upsert(_project_package())
        await uow.module_members.upsert_many(
            [_member("f", MAIN), _member("only_here", FEATURE), _member("dep", "", "dep")]
        )
        await uow.commit()
    lookup = PackageLookup(uow_factory=factory)
    served = await lookup.get_package_doc(PROJECT_PACKAGE_NAME)
    on_feature = await lookup.on_branch(FEATURE).get_package_doc(PROJECT_PACKAGE_NAME)
    assert served is not None and on_feature is not None
    assert [m.metadata["name"] for m in served.members] == ["f"]
    assert [m.metadata["name"] for m in on_feature.members] == ["only_here"]


def _project_package() -> Package:
    return Package(
        name=PROJECT_PACKAGE_NAME,
        version="0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.PROJECT,
    )


def _chunk(
    chunk_id: int,
    qname: str,
    text: str,
    start: int,
    *,
    module: str = "pkg.a",
    path: str = "pkg/a.py",
) -> Chunk:
    metadata = {
        "package": PROJECT_PACKAGE_NAME,
        "module": module,
        "qualified_name": qname,
        "source_path": path,
        "start_line": start,
        "end_line": start + 1,
    }
    return Chunk(text=text, id=chunk_id, metadata=metadata)


async def _source_factory():
    """``pkg.a.f`` differs per branch (two rows); ``pkg.a.g`` is one shared row
    the two branches hold at different spans."""
    factory = await _two_branch_factory()
    async with factory() as uow:
        await uow.chunks.insert_returning_ids(
            (
                _chunk(1, "pkg.a.f", "def f(): return 'main'", 1),
                _chunk(2, "pkg.a.f", "def f(): return 'feature'", 1),
                _chunk(3, "pkg.a.g", "def g(): pass", 4),
            )
        )
        await uow.branch_chunks.replace_membership(
            MAIN,
            [
                ChunkMembership(MAIN, 1, "pkg/a.py", 1, 2),
                ChunkMembership(MAIN, 3, "pkg/a.py", 4, 5),
            ],
        )
        await uow.branch_chunks.replace_membership(
            FEATURE,
            [
                ChunkMembership(FEATURE, 2, "pkg/a.py", 1, 2),
                ChunkMembership(FEATURE, 3, "pkg/a.py", 8, 9),
            ],
        )
        await uow.commit()
    return factory


async def test_the_target_resolver_offers_only_the_branch_names() -> None:
    """#313: a miss on main never rewrites to, nor offers, a feature/x-only symbol."""
    factory = await _source_factory()
    async with factory() as uow:
        await uow.chunks.insert_returning_ids((_chunk(4, "pkg.a.only_here", "def x(): 1", 9),))
        rows = [*await uow.branch_chunks.list_membership(FEATURE)]
        await uow.branch_chunks.replace_membership(
            FEATURE, [*rows, ChunkMembership(FEATURE, 4, "pkg/a.py", 9, 10)]
        )
        await uow.commit()
    resolver = ProjectTargetResolver(uow_factory=factory, rules=TargetResolutionConfig())
    on_feature = await resolver.on_branch(FEATURE).resolve("only_here", entry="lookup")
    on_main = await resolver.on_branch(MAIN).resolve("only_here", entry="lookup")
    assert on_feature.rewrite is not None and on_feature.rewrite.canonical == "pkg.a.only_here"
    assert on_main.rewrite is None and "pkg.a.only_here" not in on_main.candidates


async def test_a_source_root_strips_only_to_a_module_the_branch_holds() -> None:
    """#313, Rule 1: ``src.pkg.b.g`` strips to ``pkg.b.g`` only when the read
    branch holds a ``pkg.b`` row under ``src/`` — feature/x does, main does not."""
    factory = await _source_factory()
    stripped = _chunk(4, "pkg.b.g", "def g(): 1", 1, module="pkg.b", path="src/pkg/b.py")
    async with factory() as uow:
        await uow.chunks.insert_returning_ids((stripped,))
        rows = [*await uow.branch_chunks.list_membership(FEATURE)]
        await uow.branch_chunks.replace_membership(
            FEATURE, [*rows, ChunkMembership(FEATURE, 4, "src/pkg/b.py", 1, 2)]
        )
        await uow.commit()
    resolver = ProjectTargetResolver(uow_factory=factory, rules=TargetResolutionConfig())
    on_feature = await resolver.on_branch(FEATURE).resolve("src.pkg.b.g", entry="lookup")
    on_main = await resolver.on_branch(MAIN).resolve("src.pkg.b.g", entry="lookup")
    assert on_feature.rewrite is not None and on_feature.rewrite.rule == "source_root_strip"
    assert on_feature.rewrite.canonical == "pkg.b.g"
    assert on_main.rewrite is None and "pkg.b.g" not in on_main.candidates


async def test_the_symbol_source_is_the_selected_branch_row_at_its_span() -> None:
    source = SymbolSourceService(uow_factory=await _source_factory())
    on_main, _, _ = await source.source_with_items("pkg.a.f", branch=MAIN)
    on_feature, _, _ = await source.source_with_items("pkg.a.f", branch=FEATURE)
    assert "'main'" in on_main and "'feature'" in on_feature
    _, (main_row,), _ = await source.source_with_items("pkg.a.g", branch=MAIN)
    _, (feature_row,), _ = await source.source_with_items("pkg.a.g", branch=FEATURE)
    assert (main_row["start_line"], feature_row["start_line"]) == (4, 8)
    assert source.on_branch(FEATURE).branch == FEATURE


class _RankedDecisions:
    """A ``DocsSearch`` stand-in: ranks every decision chunk, records the query."""

    def __init__(self) -> None:
        self.queries: list = []

    async def ranked(self, query):
        self.queries.append(query)
        chunk = Chunk(text="t", metadata={"decision_id": 1, "package": PROJECT_PACKAGE_NAME})
        return ChunkList(items=(chunk,))


def _decision(branch: str) -> DecisionRecord:
    return DecisionRecord(
        None, PROJECT_PACKAGE_NAME, "Keep rows", "active", "adr", 1.0, (), (), (), 0.0,
        None, "verbatim", None, 1.0, 1.0, branch=branch,
    )  # fmt: skip


async def test_decision_search_pins_the_branch_and_reads_its_records() -> None:
    factory = await _two_branch_factory()
    async with factory() as uow:
        await uow.decisions.upsert([_decision(MAIN)])
        await uow.commit()
    docs = _RankedDecisions()
    decisions = DecisionService(uow_factory=factory, docs=docs)  # type: ignore[arg-type]
    _, on_main, _ = await decisions.why_search("rows", branch=MAIN)
    _, on_feature, _ = await decisions.why_search("rows", branch=FEATURE)
    _, served, _ = await decisions.why_search("rows")
    assert [len(on_main), len(on_feature), len(served)] == [1, 0, 1]
    assert [q.branch for q in docs.queries] == [MAIN, FEATURE, ""]


async def test_the_decision_dashboard_reads_the_branch() -> None:
    factory = await _two_branch_factory()
    async with factory() as uow:
        await uow.decisions.upsert([replace(_decision(MAIN), title="Only on main")])
        await uow.commit()
    decisions = DecisionService(uow_factory=factory, docs=_RankedDecisions())  # type: ignore[arg-type]
    assert "Only on main" in (await decisions.why_dashboard(branch=MAIN))[0]
    assert "Only on main" not in (await decisions.why_dashboard(branch=FEATURE))[0]


def _governs(title: str, qname: str) -> NodeReference:
    key = decision_key(title)
    return NodeReference(PROJECT_PACKAGE_NAME, f"decision:{key}", key, qname, ReferenceKind.GOVERNS)


async def test_decisions_for_a_target_follow_the_branch_edges_and_records() -> None:
    """#313 with #346: a target's governing decisions are the read branch's
    GOVERNS edges, each mapped to that branch's record. main governs ``pkg.a.f``
    by "Drop rows"; feature/x by "Keep rows", which main also holds, as another
    record (another status) — so neither the edges nor the records may come
    from the served default when feature/x is read."""
    factory = await _two_branch_factory()
    async with factory() as uow:
        await uow.decisions.upsert([
            replace(_decision(MAIN), title="Drop rows"),
            replace(_decision(MAIN), title="Keep rows"),
            replace(_decision(FEATURE), title="Keep rows", status="proposed"),
        ])  # fmt: skip
        for branch, title in ((MAIN, "Drop rows"), (FEATURE, "Keep rows")):
            edge = _governs(title, "pkg.a.f")
            await uow.references.save_many([edge], package=PROJECT_PACKAGE_NAME, branch=branch)
        await uow.commit()
    decisions = DecisionService(uow_factory=factory, docs=_RankedDecisions())  # type: ignore[arg-type]

    async def governing(branch: str | None) -> list[tuple[object, object]]:
        _, items, _ = await decisions.why_targets(["pkg.a.f"], branch=branch)
        return [(item["title"], item["status"]) for item in items]

    assert await governing(FEATURE) == [("Keep rows", "proposed")]
    assert await governing(MAIN) == [("Drop rows", "active")]
    assert await governing(None) == [("Drop rows", "active")]
