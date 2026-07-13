"""CrossRepoNavigator — boundary-crossing impact walk + hydration (AC13, AC25, AC26b)."""

from __future__ import annotations

from pydocs_mcp.application.cross_repo_navigator import (
    CrossRepoNavigator,
    NullCrossRepoNavigator,
)
from pydocs_mcp.application.reference_service import ReferenceService
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.cross_link_edge import CrossLinkEdge, WorkspaceNodeScore
from pydocs_mcp.storage.in_memory_cross_link_store import InMemoryCrossLinkStore
from pydocs_mcp.storage.node_reference import NodeReference

from .._fakes import InMemoryDecisionStore, InMemoryReferenceStore, make_fake_uow_factory


def _ref(from_node_id: str, to_node_id: str) -> NodeReference:
    return NodeReference(
        from_package="__project__",
        from_node_id=from_node_id,
        to_name=to_node_id,
        to_node_id=to_node_id,
        kind=ReferenceKind.CALLS,
    )


def _edge(from_project: str, from_node_id: str, to_project: str, to_node_id: str) -> CrossLinkEdge:
    return CrossLinkEdge(
        from_project=from_project,
        from_package="__project__",
        from_node_id=from_node_id,
        to_project=to_project,
        to_node_id=to_node_id,
        to_name=to_node_id,
        kind=ReferenceKind.CALLS,
    )


async def _plant(store: InMemoryCrossLinkStore, *edges: CrossLinkEdge) -> None:
    """Write cross edges the way WorkspaceLinker does: each project's batch
    carries EVERY edge touching it (``replace_edges_touching`` OR-deletes, so a
    per-project call with only one side of a two-way pair would drop the other).
    """
    projects = {e.from_project for e in edges} | {e.to_project for e in edges}
    for project in projects:
        touching = tuple(e for e in edges if project in (e.from_project, e.to_project))
        await store.replace_edges_touching(project, touching)


def _project_service(
    project: str, rows: tuple[NodeReference, ...], store: InMemoryCrossLinkStore
) -> ReferenceService:
    refs = InMemoryReferenceStore()
    for row in rows:
        refs.by_package.setdefault(row.from_package, []).append(row)
    return ReferenceService(
        uow_factory=make_fake_uow_factory(references=refs),
        project_name=project,
        cross_links=store,
    )


async def _workspace(
    store: InMemoryCrossLinkStore | None = None,
    *,
    max_projects: int = 8,
    workspace_scores: bool = True,
) -> tuple[CrossRepoNavigator, ReferenceService, InMemoryCrossLinkStore]:
    """Two bundles: B owns the target; A calls it through a cross edge.

    repob: b.local_caller -> b.target (local edge)
    repoa: a.caller (cross edge into b.target), a.transitive -> a.caller
    """
    store = store or InMemoryCrossLinkStore()
    svc_b = _project_service("repob", (_ref("b.local_caller", "b.target"),), store)
    svc_a = _project_service("repoa", (_ref("a.transitive", "a.caller"),), store)
    await store.replace_edges_touching("repoa", (_edge("repoa", "a.caller", "repob", "b.target"),))
    navigator = CrossRepoNavigator(
        services={"repoa": svc_a, "repob": svc_b},
        uow_factories={},
        cross_links=store,
        max_projects_per_walk=max_projects,
        workspace_scores=workspace_scores,
    )
    return navigator, svc_b, store


async def test_impact_crosses_the_boundary_with_correct_hops() -> None:
    # AC13: target hop 0 base; B-local caller hop 1; A-side caller via the
    # cross edge hop 1; A's transitive caller hop 2.
    navigator, svc_b, _ = await _workspace()
    nodes = await navigator.impact(svc_b, "__project__", "b.target", max_depth=4, limit=50)
    by_name = {(n.project or "repob", n.qualified_name): n.hop for n in nodes}
    assert by_name[("repob", "b.local_caller")] == 1
    assert by_name[("repoa", "a.caller")] == 1
    assert by_name[("repoa", "a.transitive")] == 2


async def test_impact_respects_max_depth() -> None:
    navigator, svc_b, _ = await _workspace()
    nodes = await navigator.impact(svc_b, "__project__", "b.target", max_depth=1, limit=50)
    names = {n.qualified_name for n in nodes}
    assert "a.transitive" not in names  # needs hop 2


async def _chain_navigator() -> tuple[CrossRepoNavigator, ReferenceService]:
    """Pure cross-edge chain A→B→C, no local callers bridging the hops.

    repoc owns c.target; repob's b.mid calls it (cross); repoa's a.top calls
    b.mid (cross). Reaching a.top REQUIRES probing edges_into(repob, b.mid),
    i.e. the boundary-entry node's own incoming cross edges.
    """
    store = InMemoryCrossLinkStore()
    svc_c = _project_service("repoc", (), store)
    svc_b = _project_service("repob", (), store)
    svc_a = _project_service("repoa", (), store)
    await _plant(
        store,
        _edge("repob", "b.mid", "repoc", "c.target"),
        _edge("repoa", "a.top", "repob", "b.mid"),
    )
    navigator = CrossRepoNavigator(
        services={"repoa": svc_a, "repob": svc_b, "repoc": svc_c},
        uow_factories={},
        cross_links=store,
    )
    return navigator, svc_c


async def test_impact_crosses_two_boundaries_via_cross_edges() -> None:
    # AC13 (regression): a transitive caller reached ONLY through a second
    # cross edge off a boundary-entry node must appear — the walk cannot
    # truncate at the first boundary.
    navigator, svc_c = await _chain_navigator()
    nodes = await navigator.impact(svc_c, "__project__", "c.target", max_depth=5, limit=50)
    by_key = {(n.project, n.qualified_name): n.hop for n in nodes}
    assert by_key[("repob", "b.mid")] == 1
    assert by_key[("repoa", "a.top")] == 2  # dropped before the frontier fix


async def test_cross_boundary_chain_respects_max_depth() -> None:
    # The second boundary hop is genuinely bounded by max_depth, not skipped.
    navigator, svc_c = await _chain_navigator()
    nodes = await navigator.impact(svc_c, "__project__", "c.target", max_depth=1, limit=50)
    names = {n.qualified_name for n in nodes}
    assert "b.mid" in names and "a.top" not in names  # a.top needs hop 2


async def test_cross_repo_cycle_terminates() -> None:
    # AC13: a cross cycle A.x ⇄ B.y is actually TRAVERSED (both incoming
    # cross edges probed) and terminates via the (project, qname) visited set.
    store = InMemoryCrossLinkStore()
    svc_b = _project_service("repob", (), store)
    svc_a = _project_service("repoa", (), store)
    await _plant(
        store,
        _edge("repoa", "a.x", "repob", "b.y"),
        _edge("repob", "b.y", "repoa", "a.x"),
    )
    navigator = CrossRepoNavigator(
        services={"repoa": svc_a, "repob": svc_b}, uow_factories={}, cross_links=store
    )
    nodes = await navigator.impact(svc_b, "__project__", "b.y", max_depth=6, limit=50)
    keys = {(n.project or "repob", n.qualified_name) for n in nodes}
    # The cross edge into b.y WAS probed (a.x surfaced) and the return edge
    # into a.x hit the visited target b.y, so the walk stopped — no dupes.
    assert ("repoa", "a.x") in keys
    assert len(nodes) == len(keys)


async def test_fanout_stops_at_max_projects_per_walk() -> None:
    navigator, svc_b, _ = await _workspace(max_projects=1)  # only the home project
    nodes = await navigator.impact(svc_b, "__project__", "b.target", max_depth=4, limit=50)
    assert {n.qualified_name for n in nodes} == {"b.local_caller"}


async def test_ranking_uses_workspace_scores_within_hops() -> None:
    # AC25: same-hop nodes order by workspace pagerank when present.
    navigator, svc_b, store = await _workspace()
    await store.replace_workspace_scores(
        (
            WorkspaceNodeScore(
                project="repoa", qualified_name="a.caller", pagerank=0.9, in_degree=1
            ),
            WorkspaceNodeScore(
                project="repob", qualified_name="b.local_caller", pagerank=0.1, in_degree=1
            ),
        )
    )
    nodes = await navigator.impact(svc_b, "__project__", "b.target", max_depth=4, limit=50)
    hop1 = [n.qualified_name for n in nodes if n.hop == 1]
    assert hop1 == ["a.caller", "b.local_caller"]  # 0.9 outranks 0.1


async def test_ranking_falls_back_to_in_degree_without_pagerank() -> None:
    # AC25 configuration 2: [graph] absent → pagerank is None on every
    # workspace row, so same-hop nodes order by workspace in_degree.
    navigator, svc_b, store = await _workspace()
    await store.replace_workspace_scores(
        (
            WorkspaceNodeScore(
                project="repoa", qualified_name="a.caller", pagerank=None, in_degree=1
            ),
            WorkspaceNodeScore(
                project="repob", qualified_name="b.local_caller", pagerank=None, in_degree=9
            ),
        )
    )
    nodes = await navigator.impact(svc_b, "__project__", "b.target", max_depth=4, limit=50)
    hop1 = [n.qualified_name for n in nodes if n.hop == 1]
    assert hop1 == ["b.local_caller", "a.caller"]  # in_degree 9 outranks 1


async def test_local_path_wins_over_a_returning_cross_edge() -> None:
    # AC33 impact clause: a home node reached locally is not re-attributed
    # to a longer cross path — the visited set keeps the first (local) hop.
    store = InMemoryCrossLinkStore()
    # repob: b.shared is a local caller of b.target (home hop 1).
    svc_b = _project_service("repob", (_ref("b.shared", "b.target"),), store)
    svc_a = _project_service("repoa", (), store)
    await _plant(
        store,
        _edge("repoa", "a.mid", "repob", "b.target"),
        # A returning cross edge would re-enter b.shared from repoa deeper.
        _edge("repob", "b.shared", "repoa", "a.mid"),
    )
    navigator = CrossRepoNavigator(
        services={"repoa": svc_a, "repob": svc_b}, uow_factories={}, cross_links=store
    )
    nodes = await navigator.impact(svc_b, "__project__", "b.target", max_depth=6, limit=50)
    shared = [n for n in nodes if (n.project or "repob", n.qualified_name) == ("repob", "b.shared")]
    assert len(shared) == 1 and shared[0].hop == 1  # local hop kept, not overwritten


async def test_ranking_without_workspace_scores_matches_legacy_order() -> None:
    # AC25: workspace_scores off → the pre-A1 (hop, pagerank, in_degree,
    # qname) ordering, deterministic.
    navigator, svc_b, _ = await _workspace(workspace_scores=False)
    first = await navigator.impact(svc_b, "__project__", "b.target", max_depth=4, limit=50)
    second = await navigator.impact(svc_b, "__project__", "b.target", max_depth=4, limit=50)
    assert [(n.project, n.qualified_name) for n in first] == [
        (n.project, n.qualified_name) for n in second
    ]
    hop1 = [n.qualified_name for n in first if n.hop == 1]
    assert hop1 == sorted(hop1) or hop1 == ["a.caller", "b.local_caller"]


async def test_null_navigator_returns_the_local_walk_unchanged() -> None:
    store = InMemoryCrossLinkStore()
    svc_b = _project_service("repob", (_ref("b.local_caller", "b.target"),), store)
    null = NullCrossRepoNavigator()
    nodes = await null.impact(svc_b, "__project__", "b.target", max_depth=4, limit=50)
    assert [n.qualified_name for n in nodes] == ["b.local_caller"]
    assert await null.decision_titles((("repoa", "key"),)) == {}


async def test_decision_titles_hydrate_from_the_source_project() -> None:
    # AC26(b): titles come from the SOURCE repo's decision records; a
    # project without records degrades to absence, never an error.
    from pydocs_mcp.storage.decision_record import DecisionRecord

    decisions = InMemoryDecisionStore()
    await decisions.upsert(
        [
            DecisionRecord(
                id=None,
                package="__project__",
                title="Use the streaming parser",
                status="active",
                source="adr_files",
                confidence=0.9,
                evidence=(),
                affected_files=(),
                affected_qnames=("repob.core.parse",),
                staleness_score=0.1,
                superseded_by=None,
                verification="verbatim",
                structured=None,
                created_at=0.0,
                updated_at=0.0,
            )
        ]
    )
    from pydocs_mcp.extraction.decisions.engine import decision_key

    key = decision_key("Use the streaming parser")
    navigator = CrossRepoNavigator(
        services={},
        uow_factories={"repoa": make_fake_uow_factory(decisions=decisions)},
        cross_links=InMemoryCrossLinkStore(),
    )
    titles = await navigator.decision_titles((("repoa", key), ("ghost", "nope")))
    assert titles == {("repoa", key): "Use the streaming parser"}
