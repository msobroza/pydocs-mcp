"""NullMultiVectorStore — silent writes + loud reads.

Wired by the composition root when ``late_interaction.enabled=False``.
Mirrors :class:`NullVectorStore`'s pattern but with the failure asymmetry
documented in CLAUDE.md §"Null Object pattern for optional service deps":

- writes are silent no-ops (vectors are advisory; an indexer that doesn't
  produce them shouldn't break the rest of the pipeline)
- reads raise :class:`ServiceUnavailableError` with a YAML-anchored
  pointer (callers explicitly requested late-interaction scoring; a
  silent empty result would mislead the caller)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError

if TYPE_CHECKING:
    import numpy as np


# Single source of truth for the YAML-anchored failure-mode pointer.
# End users hitting this error must be able to fix it by editing
# config; the message names the exact YAML key
# (``late_interaction.enabled``) so they don't need to read release
# notes or grep the docs. Mirrors ``_REFERENCE_GRAPH_DISABLED_MSG`` in
# :mod:`pydocs_mcp.application.null_services`.
_DISABLED_MESSAGE = (
    "Late-interaction scoring is not enabled in this deployment. Set "
    "``late_interaction.enabled: true`` in your AppConfig YAML (and "
    "install the optional extra: ``pip install "
    "'pydocs-mcp[late-interaction]'``)."
)


@dataclass(frozen=True, slots=True)
class NullMultiVectorStore:
    """No-op MultiVectorStore used when late-interaction is disabled.

    Drop-in for ``uow.multi_vectors`` so callers don't need to branch
    on backend identity. Writes are silent so an indexer that doesn't
    produce multi-vector embeddings doesn't break the pipeline; reads
    (``score``) raise so a retrieval pipeline that explicitly requests
    MaxSim scoring against an un-enabled deployment surfaces the
    actionable YAML-anchored error rather than empty results.
    """

    async def add_vectors(
        self,
        ids: Sequence[int],
        embeddings: Sequence[list[np.ndarray]],
    ) -> None:
        # Silent no-op: deployments without late-interaction skip
        # multi-vector writes during ingestion.
        return None

    async def remove_vectors(self, ids: Sequence[int]) -> None:
        return None

    async def clear_all(self) -> None:
        return None

    async def score(
        self,
        query_embedding: list[np.ndarray],
        *,
        subset_chunk_ids: Sequence[int],
        top_k: int,
    ) -> tuple[tuple[int, float], ...]:
        raise ServiceUnavailableError(_DISABLED_MESSAGE)


__all__ = ("NullMultiVectorStore",)
