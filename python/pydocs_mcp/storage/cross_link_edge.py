"""Value objects for workspace-level cross-repo linking (spec 2026-07-11 §3.2, §A1.1).

These live OUTSIDE any bundle: a cross-link is a fact about a workspace (which
sibling bundles are loaded together), persisted to the overlay sidecar — never
into a project bundle (read-only policy, spec N6/G6).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.extraction.reference_kind import ReferenceKind


@dataclass(frozen=True, slots=True)
class CrossLinkEdge:
    """A resolved reference whose source and target live in DIFFERENT bundles.

    Unlike ``NodeReference``, ``to_node_id`` is never ``None`` — an unresolved
    candidate simply isn't materialized as a cross-link. ``to_name`` keeps the
    original dotted name from the source bundle for auditability (for
    generated SIMILAR edges it mirrors the target qname).
    """

    from_project: str
    from_package: str
    from_node_id: str
    to_project: str
    to_node_id: str
    to_name: str
    kind: ReferenceKind


@dataclass(frozen=True, slots=True)
class LinkedBundleStamp:
    """What state a bundle was linked at — the staleness currency (spec §3.8).

    ``indexed_at``/``git_head`` are copied from the bundle's ``index_metadata``
    at link time; a mismatch on the next startup marks the project stale.
    """

    bundle_stem: str
    project_name: str
    bundle_path: str
    indexed_at: float
    git_head: str | None
    linked_at: float


@dataclass(frozen=True, slots=True)
class WorkspaceNodeScore:
    """One union-graph node's workspace-level score (spec §A1.1).

    ``in_degree`` is always computed (pure counting); ``pagerank`` is ``None``
    when the ``[graph]`` extra is absent — the two-tier degradation contract.
    """

    project: str
    qualified_name: str
    pagerank: float | None
    in_degree: int
