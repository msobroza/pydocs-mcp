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

from collections.abc import Callable
from dataclasses import dataclass

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.protocols import UnitOfWork


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

    async def callers(
        self,
        package: str,
        target_node_qname: str,
    ) -> tuple[NodeReference, ...]:
        """Return every ref whose ``to_node_id == target_node_qname``.

        Cross-package per spec §6.2 — the answer to "who calls X" should
        not be filtered by source package. The `package` argument is
        provided for API symmetry with `LookupService._symbol_lookup`
        (which already passes 2 args today) and for downstream rendering
        context. It is NOT used to filter results.
        """
        async with self.uow_factory() as uow:
            rows = await uow.references.find_callers(
                target_node_id=target_node_qname,
            )
        return tuple(rows)

    async def callees(
        self,
        package: str,
        from_node_qname: str,
    ) -> tuple[NodeReference, ...]:
        """Return every ref originating from ``from_node_qname``.

        Same rationale as ``callers`` — `package` is informational, the
        storage Protocol is cross-package.
        """
        async with self.uow_factory() as uow:
            rows = await uow.references.find_callees(
                from_node_id=from_node_qname,
            )
        return tuple(rows)

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
