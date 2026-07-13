"""Null ``CrossLinkStore`` — wired when linking is disabled or one bundle loads.

Per CLAUDE.md §"Null Object pattern": the consuming field is typed
``CrossLinkStore``, never ``CrossLinkStore | None``. Silent-empty (not
raising) is the correct asymmetry here — like ``NullVectorStore``,
cross-links are advisory enrichment of an answer that is already valid
bundle-locally; absence must not break ``get_references``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.cross_link_edge import (
    CrossLinkEdge,
    LinkedBundleStamp,
    WorkspaceNodeScore,
)


@dataclass(frozen=True, slots=True)
class NullCrossLinkStore:
    """Every read returns empty; every write is a silent no-op."""

    async def edges_into(
        self,
        to_project: str,
        to_node_id: str,
        *,
        kinds: tuple[ReferenceKind, ...] | None = None,
        limit: int = 200,
    ) -> tuple[CrossLinkEdge, ...]:
        return ()

    async def edges_from(
        self,
        from_project: str,
        from_node_id: str,
        *,
        kinds: tuple[ReferenceKind, ...] | None = None,
        limit: int = 200,
    ) -> tuple[CrossLinkEdge, ...]:
        return ()

    async def replace_edges_touching(self, project: str, edges: tuple[CrossLinkEdge, ...]) -> None:
        return None

    async def bundle_stamps(self) -> tuple[LinkedBundleStamp, ...]:
        return ()

    async def stamp_bundle(self, stamp: LinkedBundleStamp) -> None:
        return None

    async def replace_workspace_scores(self, rows: tuple[WorkspaceNodeScore, ...]) -> None:
        return None

    async def workspace_scores_for(
        self, pairs: tuple[tuple[str, str], ...]
    ) -> Mapping[tuple[str, str], WorkspaceNodeScore]:
        return {}
