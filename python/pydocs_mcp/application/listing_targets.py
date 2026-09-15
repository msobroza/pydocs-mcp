"""Which symbols a row listing introduces, and in which order it shows them.

A ``get_references`` page and a blast radius list EDGES and RANKED NODES, not
bodies, so the useful next step for each row is the symbol the row introduces —
the endpoint that is not the symbol the page is about. These pure functions
derive that set in the order the page shows it; ``LookupService`` narrows it to
what the follow-up tools can answer and ``application/formatting`` renders the
result as one pointer bundle (a call per row, or a single batch call once the
fan-out reaches the batch threshold).

The row ordering lives here rather than in the renderer because the bundle names
the rows a reader sees first: the order the bullets print in and the order the
pointer names them in have to be the same one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pydocs_mcp.application.mcp_inputs import is_symbol_target
from pydocs_mcp.application.reference_service import CrossReferenceRow, ImpactNode
from pydocs_mcp.storage.node_reference import NodeReference


def sorted_reference_rows(
    refs: Sequence[NodeReference | CrossReferenceRow],
) -> list[NodeReference | CrossReferenceRow]:
    """Reference rows in render order — resolved first, then by from_node_id (§A.1)."""
    return sorted(refs, key=lambda r: (0 if r.to_node_id is not None else 1, r.from_node_id))


def reference_listing_targets(
    rows: Iterable[NodeReference | CrossReferenceRow], *, target: str
) -> tuple[str, ...]:
    """The symbols a reference page's rows introduce, deduped, in render order.

    Cross-repo rows contribute nothing: their counterpart lives in ANOTHER
    project, and the follow-up tools scope to one project per call, so a pointer
    at it would advertise a name this bundle cannot address.

    Example: on a callers page for ``pkg.mod.fn``, a row
    ``pkg.other.caller → pkg.mod.fn`` contributes ``pkg.other.caller``.
    """
    local = [row for row in rows if not isinstance(row, CrossReferenceRow)]
    return _followable(_row_counterpart(row, target) for row in sorted_reference_rows(local))


def impact_listing_targets(rows: Iterable[ImpactNode], *, target: str) -> tuple[str, ...]:
    """The symbols a blast radius lists, deduped, in render order.

    Render order is hop ring by hop ring, the service's rank order kept inside
    each ring, so the first names are the ones that break first. A node carrying
    a ``project`` came from another repo's graph — skipped for the same reason
    cross-repo reference rows are.
    """
    local = sorted((node for node in rows if not node.project), key=lambda node: node.hop)
    return _followable(node.qualified_name for node in local if node.qualified_name != target)


def _row_counterpart(row: NodeReference | CrossReferenceRow, target: str) -> str:
    """The endpoint of ``row`` that is not ``target``.

    One rule covers every direction: a callers row names its caller on the
    from-side, a callees row names its callee on the to-side, and an inheritance
    row switches sides with its sense (a base is the to-side of an edge out of
    the target, a subclass the from-side of an edge into it). An unresolved
    to-side yields ``""`` — the captured name matched no indexed symbol, so no
    tool could answer a call about it.
    """
    if row.from_node_id != target:
        return row.from_node_id
    return row.to_node_id or ""


def _followable(names: Iterable[str]) -> tuple[str, ...]:
    """The names a follow-up call can address, deduped, order kept.

    Filters what the tools' own validator rejects — a synthetic
    ``decision:<key>`` node on a ``governed_by`` page, an empty counterpart — so
    one unaddressable row can never take a whole batch call down with it.
    """
    return tuple(dict.fromkeys(name for name in names if is_symbol_target(name)))
