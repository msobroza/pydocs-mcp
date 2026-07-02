"""Per-database index identity — the single-row ``index_metadata`` table.

One :class:`IndexMetadata` per ``.db`` records who built it: the project name +
root (multi-repo routing key), the embedder identity (model / dim / provider —
so a loader can REJECT a ``.tq`` built with a different embedder before it panics
at query time), the ingestion ``pipeline_hash``, and ``indexed_at`` (the
most-recent-wins tiebreak when the same dependency appears in several loaded
repos). Written at index time; read at serve/search time.

Old databases (built before this table existed) have no row — callers use
:meth:`IndexMetadata.legacy_fallback` to synthesize one from the pre-existing
``packages.embedding_model`` column (dim unknown, ``indexed_at=0.0`` so it always
loses the most-recent tiebreak).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IndexMetadata:
    """Identity of one indexed database (single row of ``index_metadata``)."""

    project_name: str
    project_root: str
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    pipeline_hash: str
    indexed_at: float

    @classmethod
    def legacy_fallback(cls, *, project_name: str, embedding_model: str | None) -> IndexMetadata:
        """Synthesize metadata for a pre-``index_metadata`` database.

        Only the embedder model name was persisted then (``packages.embedding_model``),
        so ``embedding_dim`` is ``-1`` (unknown — the dim check is skipped, only the
        model-name check runs) and ``indexed_at`` is ``0.0`` (always loses the
        most-recent tiebreak against a freshly-stamped database).
        """
        return cls(
            project_name=project_name,
            project_root="",
            embedding_provider="",
            embedding_model=embedding_model or "",
            embedding_dim=-1,
            pipeline_hash="",
            indexed_at=0.0,
        )

    def embedder_matches(self, *, model: str, dim: int) -> bool:
        """True if this database's vectors are usable by a ``(model, dim)`` embedder.

        An empty ``embedding_model`` means the db never recorded its embedder
        identity (a very old db with no ``packages.embedding_model``); it cannot be
        validated, so it is permitted rather than false-rejected. Otherwise the
        model name must match, and the dim must match too UNLESS it is unknown
        (``-1``, a legacy stamp) — an unknown dim can't be checked, so a matching
        model name gates it alone (same model implies same dim in practice).
        """
        if not self.embedding_model:
            return True
        if self.embedding_model != model:
            return False
        return self.embedding_dim in (-1, dim)
