"""In-memory ``CrossLinkStore`` — EROFS degradation mode + test fake (spec §3.8).

Same read/write semantics as the SQLite overlay, nothing persisted. This IS
Alternative B scoped to the case where persistence is impossible: the
``WorkspaceLinker`` runs identically against either impl.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.cross_link_edge import (
    CrossLinkEdge,
    LinkedBundleStamp,
    WorkspaceNodeScore,
)


def _sort_key(e: CrossLinkEdge) -> tuple[str, str, str, str, str]:
    return (e.from_project, e.from_node_id, e.to_project, e.to_node_id, str(e.kind))


@dataclass(slots=True)
class InMemoryCrossLinkStore:
    """Dict-backed ``CrossLinkStore`` mirroring the SQLite overlay's semantics."""

    _edges: dict[tuple[str, str, str, str, str], CrossLinkEdge] = field(default_factory=dict)
    _stamps: dict[str, LinkedBundleStamp] = field(default_factory=dict)
    _scores: dict[tuple[str, str], WorkspaceNodeScore] = field(default_factory=dict)

    async def edges_into(
        self,
        to_project: str,
        to_node_id: str,
        *,
        kinds: tuple[ReferenceKind, ...] | None = None,
        limit: int = 200,
    ) -> tuple[CrossLinkEdge, ...]:
        matches = [
            e
            for e in self._edges.values()
            if e.to_project == to_project and e.to_node_id == to_node_id
        ]
        return _filtered(matches, kinds, limit)

    async def edges_from(
        self,
        from_project: str,
        from_node_id: str,
        *,
        kinds: tuple[ReferenceKind, ...] | None = None,
        limit: int = 200,
    ) -> tuple[CrossLinkEdge, ...]:
        matches = [
            e
            for e in self._edges.values()
            if e.from_project == from_project and e.from_node_id == from_node_id
        ]
        return _filtered(matches, kinds, limit)

    async def replace_edges_touching(self, project: str, edges: tuple[CrossLinkEdge, ...]) -> None:
        self._edges = {
            key: e
            for key, e in self._edges.items()
            if e.from_project != project and e.to_project != project
        }
        for e in edges:
            self._edges[_sort_key(e)] = e

    async def bundle_stamps(self) -> tuple[LinkedBundleStamp, ...]:
        return tuple(self._stamps[k] for k in sorted(self._stamps))

    async def stamp_bundle(self, stamp: LinkedBundleStamp) -> None:
        self._stamps[stamp.bundle_stem] = stamp

    async def delete_stamp(self, bundle_stem: str) -> None:
        self._stamps.pop(bundle_stem, None)

    async def replace_workspace_scores(self, rows: tuple[WorkspaceNodeScore, ...]) -> None:
        self._scores = {(r.project, r.qualified_name): r for r in rows}

    async def workspace_scores_for(
        self, pairs: tuple[tuple[str, str], ...]
    ) -> Mapping[tuple[str, str], WorkspaceNodeScore]:
        return {pair: self._scores[pair] for pair in pairs if pair in self._scores}


def _filtered(
    matches: list[CrossLinkEdge],
    kinds: tuple[ReferenceKind, ...] | None,
    limit: int,
) -> tuple[CrossLinkEdge, ...]:
    if kinds is not None:
        wanted = {str(k) for k in kinds}
        matches = [e for e in matches if str(e.kind) in wanted]
    return tuple(sorted(matches, key=_sort_key)[:limit])
