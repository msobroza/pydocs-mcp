"""Hop-wise federation of the impact walk across bundles (spec §3.4b, §A1.1).

Single-bundle traversal stays inside each ``ReferenceService`` (the recursive
CTE); this coordinator owns the WORKSPACE walk: after each hop it consults the
overlay for boundary crossings, seeds the source project's own walk with the
remaining depth budget, and terminates on a global ``(project, qname)``
visited set. Ranking follows the ONE canonical chain (§A1.1): hop asc →
workspace pagerank desc (when present) → workspace in_degree desc (when
workspace scores are on) → per-bundle scores (the pre-A1 legacy fallback,
never compared across bundles) → ``(project, qname)`` asc.

Also owns cross-repo decision hydration (§A1.2): a governed_by cross row's
decision record lives in the SOURCE repo's ``decision_records``; titles are
read through that project's UoW — an absent record degrades to a key-only
row, never an error.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from pydocs_mcp.application.reference_service import ImpactNode, ReferenceService
from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.storage.cross_link_edge import WorkspaceNodeScore
from pydocs_mcp.storage.null_cross_link_store import NullCrossLinkStore
from pydocs_mcp.storage.protocols import CrossLinkStore, UnitOfWork


@dataclass(frozen=True, slots=True)
class NullCrossRepoNavigator:
    """Disabled/single-bundle stand-in: the local walk, unchanged.

    Null Object per CLAUDE.md — consumers hold a navigator, never
    ``Navigator | None``. ``decision_titles`` is empty (nothing to hydrate).
    """

    async def impact(
        self,
        service: ReferenceService,
        package: str,
        qname: str,
        *,
        max_depth: int,
        limit: int,
    ) -> tuple[ImpactNode, ...]:
        return await service.impact(package, qname, max_depth=max_depth, limit=limit)

    async def decision_titles(
        self, wanted: tuple[tuple[str, str], ...]
    ) -> Mapping[tuple[str, str], str]:
        return {}


@dataclass(slots=True)
class _Walk:
    """Mutable state of one workspace impact walk (bounded, deterministic)."""

    visited: set[tuple[str, str]]
    collected: dict[tuple[str, str], ImpactNode]
    projects_entered: set[str]
    frontier: list[tuple[str, str, int]]


@dataclass(frozen=True, slots=True)
class CrossRepoNavigator:
    """Impact federation + decision hydration over the loaded workspace."""

    services: Mapping[str, ReferenceService]
    uow_factories: Mapping[str, Callable[[], UnitOfWork]]
    cross_links: CrossLinkStore = field(default_factory=NullCrossLinkStore)
    max_projects_per_walk: int = 8
    workspace_scores: bool = True

    async def impact(
        self,
        service: ReferenceService,
        package: str,
        qname: str,
        *,
        max_depth: int,
        limit: int,
    ) -> tuple[ImpactNode, ...]:
        """Workspace blast-radius: the local reverse walk crossing boundaries.

        The target project is ``service.project_name``. Bundle-local
        expansion reuses each project's CTE-backed ``impact`` with the
        remaining depth budget; overlay ``edges_into`` provide the boundary
        hops. Deterministic and bounded: global ``(project, qname)`` visited
        set, ``max_projects_per_walk`` fan-out cap, ``max_depth`` budget.
        """
        home = service.project_name
        visited: set[tuple[str, str]] = {(home, qname)}
        collected: dict[tuple[str, str], ImpactNode] = {}
        projects_entered = {home}
        # frontier: nodes discovered at hop h whose boundary crossings still
        # need probing. Seeded with the target itself at hop 0.
        frontier: list[tuple[str, str, int]] = [(home, qname, 0)]

        local = await service.impact(package, qname, max_depth=max_depth, limit=limit)
        for node in local:
            key = (home, node.qualified_name)
            visited.add(key)
            collected[key] = node
            frontier.append((home, node.qualified_name, node.hop))

        walk = _Walk(
            visited=visited,
            collected=collected,
            projects_entered=projects_entered,
            frontier=frontier,
        )
        while walk.frontier:
            project, node_qname, hop = walk.frontier.pop(0)
            if hop >= max_depth:
                continue
            edges = await self.cross_links.edges_into(project, node_qname)
            for edge in edges:
                await self._cross_boundary(walk, edge, hop, max_depth=max_depth, limit=limit)

        ranked = await self._rank(home, tuple(walk.collected.values()))
        return ranked[:limit]

    async def _cross_boundary(
        self,
        walk: _Walk,
        edge: object,
        hop: int,
        *,
        max_depth: int,
        limit: int,
    ) -> None:
        """One overlay edge: enter the source project, absorb its sub-walk."""
        source = edge.from_project  # type: ignore[attr-defined]
        from_node = edge.from_node_id  # type: ignore[attr-defined]
        key = (source, from_node)
        if key in walk.visited:
            return
        if (
            source not in walk.projects_entered
            and len(walk.projects_entered) >= self.max_projects_per_walk
        ):
            return
        walk.projects_entered.add(source)
        walk.visited.add(key)
        entry_hop = hop + 1
        walk.collected[key] = ImpactNode(
            qualified_name=from_node,
            hop=entry_hop,
            pagerank=0.0,
            in_degree=0,
            has_scores=False,
            project=source,
        )
        # The entry node itself has boundary crossings to probe: a caller in a
        # THIRD project reaches it through another cross edge (A→B→C chains,
        # and cross cycles routed back through an entry node). Feeding it to
        # the frontier is what lets the main loop query edges_into(source,
        # from_node); without it the walk truncates at the first boundary
        # (the depth guard in the main loop still bounds it).
        walk.frontier.append((source, from_node, entry_hop))
        remaining = max_depth - entry_hop
        sub_service = self.services.get(source)
        if remaining <= 0 or sub_service is None:
            return
        sub = await sub_service.impact(
            PROJECT_PACKAGE_NAME, from_node, max_depth=remaining, limit=limit
        )
        for node in sub:
            sub_key = (source, node.qualified_name)
            if sub_key in walk.visited:
                continue
            walk.visited.add(sub_key)
            total_hop = entry_hop + node.hop
            walk.collected[sub_key] = ImpactNode(
                qualified_name=node.qualified_name,
                hop=total_hop,
                pagerank=node.pagerank,
                in_degree=node.in_degree,
                has_scores=node.has_scores,
                project=source,
            )
            walk.frontier.append((source, node.qualified_name, total_hop))

    async def _rank(self, home: str, nodes: tuple[ImpactNode, ...]) -> tuple[ImpactNode, ...]:
        """The §A1.1 canonical ordering chain, deterministic in every mode."""
        ws: Mapping[tuple[str, str], WorkspaceNodeScore] = {}
        if self.workspace_scores:
            pairs = tuple((node.project or home, node.qualified_name) for node in nodes)
            ws = await self.cross_links.workspace_scores_for(pairs)

        def sort_key(node: ImpactNode) -> tuple[int, float, int, float, int, str, str]:
            project = node.project or home
            score = ws.get((project, node.qualified_name))
            ws_pagerank = (
                score.pagerank if score is not None and score.pagerank is not None else 0.0
            )
            ws_in_degree = score.in_degree if score is not None else 0
            # Legacy per-bundle scores rank ONLY nodes without a workspace
            # row — never compared across bundles meaningfully; with
            # workspace scores off this collapses to the pre-A1 ordering.
            legacy_pagerank = node.pagerank if score is None else 0.0
            legacy_in_degree = node.in_degree if score is None else 0
            return (
                node.hop,
                -ws_pagerank,
                -ws_in_degree,
                -legacy_pagerank,
                -legacy_in_degree,
                project,
                node.qualified_name,
            )

        return tuple(sorted(nodes, key=sort_key))

    async def decision_titles(
        self, wanted: tuple[tuple[str, str], ...]
    ) -> Mapping[tuple[str, str], str]:
        """Titles for ``(project, decision_key)`` pairs — degraded on absence.

        Reads each source project's ``decision_records`` through its own UoW
        (the routed per-project store); a project without records — or with
        decision capture disabled — simply contributes no titles, and the
        caller renders key-only rows (§A1.2 'degraded, never an error').
        """
        wanted_set = set(wanted)
        out: dict[tuple[str, str], str] = {}
        for project in {p for p, _ in wanted_set}:
            factory = self.uow_factories.get(project)
            if factory is None:
                continue
            async with factory() as uow:
                records = await uow.decisions.list_for_package(PROJECT_PACKAGE_NAME)
            for record in records:
                key = (project, decision_key(record.title))
                if key in wanted_set:
                    out[key] = record.title
        return out
