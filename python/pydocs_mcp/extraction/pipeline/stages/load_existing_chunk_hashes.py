"""LoadExistingChunkHashesStage — read SQLite for the package's existing chunk hashes.

Populates :attr:`IngestionState.existing_chunk_hashes` so
:class:`EmbedChunksStage` can skip embedding chunks whose hash is already
in the DB. Runs after :class:`AssignChunkContentHashStage` (chunks have
the pipeline-aware hash) and before :class:`EmbedChunksStage`.

Excludes rows with NULL / empty ``content_hash`` (pre-migration legacy
rows) so those self-heal on the first reindex per package — they fall
into the 'added' bucket of the diff-merge and get re-embedded
(spec AC-8).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import ChunkFilterField
from pydocs_mcp.storage.protocols import UnitOfWork


@stage_registry.register("load_existing_chunk_hashes")
@dataclass(frozen=True, slots=True)
class LoadExistingChunkHashesStage:
    """Read SQLite for the current package's existing chunk hashes."""

    uow_factory: Callable[[], UnitOfWork] | None = None
    name: str = "load_existing_chunk_hashes"

    async def run(self, state: IngestionState) -> IngestionState:
        # No chunks → nothing for the downstream embed gate to skip;
        # no factory → test path with no composition root;
        # no package → nothing to scope the query to.
        if (
            not state.chunks.chunks
            or self.uow_factory is None
            or state.package is None
        ):
            return state
        async with self.uow_factory() as uow:
            pairs = await uow.chunks.list_id_hash_pairs(
                filter={ChunkFilterField.PACKAGE.value: state.package.name},
            )
        # Exclude NULL/empty content_hash rows (legacy / pre-migration);
        # they need re-embedding so they belong in the 'added' bucket of
        # the diff-merge — keeping them in the skip set would silently
        # preserve broken pre-migration vectors.
        existing = {h: cid for cid, h in pairs if h}
        return replace(state, existing_chunk_hashes=existing)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> LoadExistingChunkHashesStage:
        uow_factory = getattr(context, "uow_factory", None)
        if uow_factory is None:
            raise ValueError(
                "LoadExistingChunkHashesStage requires BuildContext.uow_factory "
                "to be set. Production wiring in __main__.py / server.py sets "
                "this from the composite UoW factory; tests must pass it "
                "explicitly.",
            )
        return cls(uow_factory=uow_factory)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "load_existing_chunk_hashes"}


__all__ = ("LoadExistingChunkHashesStage",)
