"""Governance dashboard view-model for ``get_why()`` dashboard mode (spec §D11).

:class:`DecisionDashboard` is the frozen view-model
``DecisionService.why_dashboard`` builds and
``application/formatting.format_decision_dashboard`` renders — formatting
imports it only under ``TYPE_CHECKING`` so it stays a pure rendering module.
:func:`build_decision_dashboard` assembles it from the records and centrality
signals one UoW read gathers (pure — no I/O).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydocs_mcp.storage.decision_record import DecisionRecord
    from pydocs_mcp.storage.node_score import NodeScore

# Top-N caps for the governance dashboard lists (spec §D11). Stalest-active and
# awaiting-review lists show five each; ungoverned modules show five. Single
# source so the slice widths never drift from the renderer's expectations.
_DASHBOARD_LIST_LIMIT = 5

# Lifecycle state that marks a decision as awaiting human review (spec §D9).
_PROPOSED_STATUS = "proposed"
_ACTIVE_STATUS = "active"


@dataclass(frozen=True, slots=True)
class DecisionDashboard:
    """Governance view-model for ``get_why`` dashboard mode (spec §D11).

    Fields are already sliced/ranked by the service — the renderer only lays
    them out. ``stalest`` / ``awaiting_review`` are capped at 5 by the service;
    ``ungoverned_modules`` are the top-centrality module qnames with no
    decision coverage (up to 5).
    """

    by_status: Mapping[str, int]
    by_source: Mapping[str, int]
    stalest: tuple[DecisionRecord, ...]
    awaiting_review: tuple[DecisionRecord, ...]
    ungoverned_modules: tuple[str, ...]


def build_decision_dashboard(
    records: Sequence[DecisionRecord],
    scores: Sequence[NodeScore],
    degrees: Mapping[str, tuple[int, int]],
    governed: frozenset[str],
) -> DecisionDashboard:
    """Assemble the governance :class:`DecisionDashboard` (pure — no I/O).

    ``governed`` is the resolver-backed GOVERNS anti-join set (qnames with an
    inbound GOVERNS edge, §D18) — the ungoverned list is the top-centrality
    modules NOT in it.
    """
    by_status = _count_by(records, key=lambda r: r.status)
    by_source = _count_by(records, key=lambda r: r.source)
    active = [r for r in records if r.status == _ACTIVE_STATUS]
    active.sort(key=lambda r: (-r.staleness_score, r.id or 0))
    proposed = [r for r in records if r.status == _PROPOSED_STATUS]
    proposed.sort(key=lambda r: (-r.staleness_score, r.id or 0))
    ungoverned = _ungoverned_modules(scores, degrees, governed)
    return DecisionDashboard(
        by_status=by_status,
        by_source=by_source,
        stalest=tuple(active[:_DASHBOARD_LIST_LIMIT]),
        awaiting_review=tuple(proposed[:_DASHBOARD_LIST_LIMIT]),
        ungoverned_modules=ungoverned,
    )


def _count_by(
    records: Sequence[DecisionRecord],
    *,
    key: Callable[[DecisionRecord], str],
) -> dict[str, int]:
    """Tally ``records`` by the ``key`` projection (status or source)."""
    counts: dict[str, int] = {}
    for record in records:
        bucket = key(record)
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def _ungoverned_modules(
    scores: Sequence[NodeScore],
    degrees: Mapping[str, tuple[int, int]],
    governed: frozenset[str],
) -> tuple[str, ...]:
    """Top-centrality module qnames with no inbound GOVERNS edge (§D18 anti-join).

    Centrality source mirrors :class:`OverviewService`: pagerank when node scores
    exist, else the reference in-degree proxy — the shared §D6/§D11 degradation
    rule. Modules with an inbound GOVERNS edge (``governed``) are excluded; the
    result is the top ``_DASHBOARD_LIST_LIMIT`` ungoverned qnames by descending
    centrality.
    """
    if scores:
        ranking = [(s.pagerank, s.qualified_name) for s in scores]
    else:
        ranking = [(float(in_deg), qname) for qname, (in_deg, _out) in degrees.items()]
    uncovered = [(rank, qname) for rank, qname in ranking if qname not in governed]
    uncovered.sort(key=lambda rq: (-rq[0], rq[1]))
    return tuple(qname for _rank, qname in uncovered[:_DASHBOARD_LIST_LIMIT])
