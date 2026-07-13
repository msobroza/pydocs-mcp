"""ReferenceService — read-side wrapper over the reference graph (spec §8.1).

Follows the CLAUDE.md §"Creating new application services" contract:
single ``uow_factory`` constructor parameter, per-method UoW open/close,
reads only (no ``await uow.commit()``). The ``__aexit__`` safety-net
rollback is a no-op for read-only paths.

#5b ships the service; #5c wires it into ``LookupService.ref_svc`` and
flips ``LookupService._symbol_lookup`` to invoke it for
``show="callers"|"callees"``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.cross_link_edge import CrossLinkEdge
from pydocs_mcp.storage.filters import FieldIn
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.null_cross_link_store import NullCrossLinkStore
from pydocs_mcp.storage.protocols import CrossLinkStore, UnitOfWork


@dataclass(frozen=True, slots=True)
class CrossReferenceRow:
    """A cross-repo edge rendered alongside bundle-local rows (spec §3.4/§3.6).

    Attribute names mirror ``NodeReference`` where shared so the rendering
    layer duck-types both; the two project fields carry the qualifier.
    Always resolved by construction — an unresolved candidate never
    materializes as a cross-link.
    """

    from_project: str
    from_package: str
    from_node_id: str
    to_project: str
    to_node_id: str
    to_name: str
    kind: ReferenceKind


def _cross_row(edge: CrossLinkEdge) -> CrossReferenceRow:
    return CrossReferenceRow(
        from_project=edge.from_project,
        from_package=edge.from_package,
        from_node_id=edge.from_node_id,
        to_project=edge.to_project,
        to_node_id=edge.to_node_id,
        to_name=edge.to_name,
        kind=edge.kind,
    )


@dataclass(frozen=True, slots=True)
class ContextNode:
    """One symbol in a ``lookup(show="context")`` graded-fidelity pack.

    ``hop`` is the forward distance from the seed (``0`` = the seed itself =
    focus tier; ``1`` = ring tier; ``>= 2`` = outline tier). ``source_text`` is
    the symbol's full source (the only content the chunk store persists), from
    which the renderer derives each tier's fidelity: full source (focus), first
    line / signature (ring), or just the name (outline). ``pagerank`` /
    ``in_degree`` drive the within-budget inclusion order (closer + more
    central first).
    """

    qualified_name: str
    hop: int
    pagerank: float
    in_degree: int
    source_text: str


@dataclass(frozen=True, slots=True)
class ImpactNode:
    """One transitive caller in a ``lookup(show="impact")`` blast-radius.

    ``hop`` is the shortest reverse distance from the target; ``in_degree`` is
    the node's structural fan-in; ``pagerank`` is its graph centrality (``0.0``
    when ``node_scores`` is disabled); ``has_scores`` records whether PageRank
    was available (drives the render label + the fan-in-vs-PageRank ranking).
    """

    qualified_name: str
    hop: int
    pagerank: float
    in_degree: int
    has_scores: bool
    # Owning project for cross-repo rows ("" = the routed bundle) — set by
    # the CrossRepoNavigator; single-bundle walks never populate it.
    project: str = ""


@dataclass(frozen=True, slots=True)
class ReferenceService:
    """Reads the cross-node reference graph through a per-call UnitOfWork.

    All three public methods open a fresh UoW, read via ``uow.references``,
    and return tuples (not lists — frozen+hashable contract for the
    rendering layer downstream).

    **2-arg signature note (controller decision C1):** `callers` and
    `callees` take ``(package, target_node_qname)`` to match the existing
    `LookupService._symbol_lookup` call site in
    `tests/application/test_lookup_service.py:357,381`. The `package`
    argument is informational — the underlying `uow.references.find_*`
    calls remain cross-package per spec §6.2 (no package filter). This
    fixes a spec/test inconsistency in §8.1 (spec was 1-arg). Task 20
    amends the spec to match.
    """

    uow_factory: Callable[[], UnitOfWork]
    # Cross-repo federation (spec §3.4a): the owning project's name and the
    # workspace overlay. Defaults keep single-project construction (and every
    # existing call site) byte-identical — NullCrossLinkStore makes every
    # union degenerate to today's behavior at the cost of one no-op call.
    project_name: str = ""
    cross_links: CrossLinkStore = field(default_factory=NullCrossLinkStore)

    async def callers(
        self,
        package: str,
        target_node_qname: str,
    ) -> tuple[NodeReference | CrossReferenceRow, ...]:
        """Return every ref whose ``to_node_id == target_node_qname``.

        Cross-package per spec §6.2 — the answer to "who calls X" should
        not be filtered by source package. The `package` argument is
        provided for API symmetry with `LookupService._symbol_lookup`
        (which already passes 2 args today) and for downstream rendering
        context. It is NOT used to filter results.

        Cross-repo union (spec §3.4a): overlay ``edges_into`` this project's
        target append AFTER the local rows (local-first ordering, §A1.8),
        deduped local-wins on ``(from_node_id, to_node_id, kind)``.
        """
        async with self.uow_factory() as uow:
            rows = await uow.references.find_callers(
                target_node_id=target_node_qname,
            )
        cross = await self.cross_links.edges_into(self.project_name, target_node_qname)
        return (*rows, *self._deduped(rows, cross))

    async def callees(
        self,
        package: str,
        from_node_qname: str,
    ) -> tuple[NodeReference | CrossReferenceRow, ...]:
        """Return every ref originating from ``from_node_qname``.

        Same rationale as ``callers`` — `package` is informational, the
        storage Protocol is cross-package.

        Cross-repo substitution (spec §3.4a): a local UNRESOLVED row whose
        ``(to_name, kind)`` matches an overlay ``edges_from`` entry is
        returned as the resolved, project-qualified cross row instead; a
        cross edge matching an already-RESOLVED local row is suppressed
        (the §A1.8 read-side dedup — local wins).
        """
        async with self.uow_factory() as uow:
            rows = await uow.references.find_callees(
                from_node_id=from_node_qname,
            )
        cross = await self.cross_links.edges_from(self.project_name, from_node_qname)
        if not cross:
            return tuple(rows)
        by_name_kind = {(e.to_name, str(e.kind)): e for e in cross}
        resolved_keys = {(r.to_node_id, str(r.kind)) for r in rows if r.to_node_id is not None}
        out: list[NodeReference | CrossReferenceRow] = []
        for row in rows:
            match = (
                by_name_kind.get((row.to_name, str(row.kind))) if row.to_node_id is None else None
            )
            if match is not None and (match.to_node_id, str(match.kind)) not in resolved_keys:
                out.append(_cross_row(match))
            else:
                out.append(row)
        return tuple(out)

    async def find_by_name(
        self,
        name: str,
        *,
        kind: ReferenceKind | None = None,
    ) -> tuple[NodeReference, ...]:
        """Find every ref whose ``to_name == name`` (queryable for both
        resolved AND unresolved edges — that's the whole point of keeping
        unresolved rows queryable)."""
        async with self.uow_factory() as uow:
            rows = await uow.references.find_by_name(name, kind)
        return tuple(rows)

    async def governed_by(
        self,
        package: str,
        node_qname: str,
    ) -> tuple[NodeReference | CrossReferenceRow, ...]:
        """GOVERNS edges pointing AT ``node_qname`` — its governing decisions (§D18).

        The inbound GOVERNS edges (``from_node_id='decision:<key>'``,
        ``to_node_id == node_qname``, ``kind='governs'``) render as reference
        rows so ``get_references(direction='governed_by')`` answers "which
        decisions govern this symbol?" through the same surface as callers /
        callees. Resolver-backed (matches on ``to_node_id``, so an unresolved
        GOVERNS edge naming this qname is excluded). ``package`` is informational
        (governance is cross-package, like ``callers``). Read-only.
        """
        async with self.uow_factory() as uow:
            rows = await uow.references.find_callers(target_node_id=node_qname)
        local = tuple(r for r in rows if r.kind is ReferenceKind.GOVERNS)
        cross = await self.cross_links.edges_into(
            self.project_name, node_qname, kinds=(ReferenceKind.GOVERNS,)
        )
        return (*local, *self._deduped(local, cross))

    async def inherits(
        self,
        package: str,
        node_qname: str,
    ) -> tuple[NodeReference | CrossReferenceRow, ...]:
        """INHERITS rows naming ``node_qname`` ∪ overlay INHERITS edges into it.

        The local read stays name-keyed (``find_by_name`` — unresolved rows
        included, that is its point); the cross union adds resolved
        project-qualified subclasses from sibling bundles (spec §3.4a).
        ``package`` is informational, as everywhere on this service.
        """
        local = await self.find_by_name(node_qname, kind=ReferenceKind.INHERITS)
        cross = await self.cross_links.edges_into(
            self.project_name, node_qname, kinds=(ReferenceKind.INHERITS,)
        )
        return (*local, *self._deduped(local, cross))

    @staticmethod
    def _deduped(
        local: Sequence[NodeReference],
        cross: tuple[CrossLinkEdge, ...],
    ) -> tuple[CrossReferenceRow, ...]:
        """Cross rows minus any duplicating a local row (§A1.8: local wins).

        Dedup key ``(from_node_id, to_qname, kind)`` — the transient window
        where a stale overlay edge coexists with a row the bundle has since
        resolved locally.
        """
        seen = {
            (r.from_node_id, r.to_node_id, str(r.kind)) for r in local if r.to_node_id is not None
        }
        return tuple(
            _cross_row(e) for e in cross if (e.from_node_id, e.to_node_id, str(e.kind)) not in seen
        )

    async def impact(
        self,
        package: str,
        qname: str,
        *,
        max_depth: int,
        limit: int,
    ) -> tuple[ImpactNode, ...]:
        """Ranked blast-radius: who transitively calls ``qname`` (what breaks).

        Walks the reference graph BACKWARD up to ``max_depth`` hops, then ranks
        by ``(hop asc, pagerank desc, in_degree desc, qname asc)`` and slices to
        ``limit``. PageRank comes from the index-time ``node_scores`` table when
        enabled; otherwise every ``pagerank`` is ``0.0`` and the sort collapses
        to fan-in (in-degree) ranking — no ``[graph]`` extra required. ``package``
        is informational (the walk is cross-package, like ``callers``).
        Read-only: no ``commit``.
        """
        async with self.uow_factory() as uow:
            discovered = await uow.references.find_transitive_callers(
                qname,
                max_depth=max_depth,
            )
            if not discovered:
                return ()
            scores = await uow.node_scores.scores_for([q for q, _hop, _deg in discovered])
        nodes = [
            ImpactNode(
                qualified_name=q,
                hop=hop,
                pagerank=scores[q].pagerank if q in scores else 0.0,
                in_degree=scores[q].in_degree if q in scores else fan_in,
                has_scores=q in scores,
            )
            for q, hop, fan_in in discovered
        ]
        nodes.sort(key=lambda n: (n.hop, -n.pagerank, -n.in_degree, n.qualified_name))
        return tuple(nodes[:limit])

    async def context(
        self,
        package: str,
        qname: str,
        *,
        max_depth: int,
        limit: int,
    ) -> tuple[ContextNode, ...]:
        """Smart-context: the seed's dependency closure, ready for graded packing.

        Walks FORWARD up to ``max_depth`` hops (what ``qname`` transitively
        calls), ranks the callees by ``(hop asc, pagerank desc, in_degree desc,
        qname)``, keeps the seed (hop 0) plus the top ``limit - 1`` callees, and
        hydrates each with its source text from the chunk store (the only
        content persisted; the renderer derives the signature/outline tiers
        from it). The seed is ALWAYS first (the focus). PageRank comes from
        ``node_scores``
        when enabled; otherwise ranking collapses to fan-in. Rendering
        (``format_context``) packs these under the token budget at graded
        fidelity. Read-only; ``package`` informational (cross-package walk).
        """
        async with self.uow_factory() as uow:
            callees = await uow.references.find_transitive_callees(qname, max_depth=max_depth)
            scores = await uow.node_scores.scores_for([qname, *(q for q, _, _ in callees)])
            callees.sort(
                key=lambda t: (
                    t[1],
                    -(scores[t[0]].pagerank if t[0] in scores else 0.0),
                    -t[2],
                    t[0],
                )
            )
            seed_deg = scores[qname].in_degree if qname in scores else 0
            selected = [(qname, 0, seed_deg), *callees[: max(0, limit - 1)]]
            by_qname = {
                c.metadata.get("qualified_name"): c
                for c in await uow.chunks.list(
                    filter=FieldIn(field="qualified_name", values=tuple(q for q, _, _ in selected)),
                )
            }
        return tuple(
            self._to_context_node(q, hop, fan_in, scores.get(q), by_qname.get(q))
            for q, hop, fan_in in selected
        )

    @staticmethod
    def _to_context_node(qname, hop, fan_in, score, chunk) -> ContextNode:
        return ContextNode(
            qualified_name=qname,
            hop=hop,
            pagerank=score.pagerank if score is not None else 0.0,
            in_degree=score.in_degree if score is not None else fan_in,
            source_text=chunk.text if chunk is not None else "",
        )
