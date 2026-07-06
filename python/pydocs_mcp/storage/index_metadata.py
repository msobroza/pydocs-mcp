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

import sqlite3
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
    git_head: str = ""

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


# ── Row mappers (single-row ``index_metadata`` table) ────────────────────


def write_index_metadata(connection: sqlite3.Connection, meta: IndexMetadata) -> None:
    """Upsert the single ``index_metadata`` row (id=1) that stamps this database."""
    connection.execute(
        "INSERT INTO index_metadata "
        "(id, project_name, project_root, embedding_provider, embedding_model, "
        "embedding_dim, pipeline_hash, indexed_at, git_head) VALUES (1,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "project_name=excluded.project_name, project_root=excluded.project_root, "
        "embedding_provider=excluded.embedding_provider, "
        "embedding_model=excluded.embedding_model, embedding_dim=excluded.embedding_dim, "
        "pipeline_hash=excluded.pipeline_hash, indexed_at=excluded.indexed_at, "
        "git_head=excluded.git_head",
        (
            meta.project_name,
            meta.project_root,
            meta.embedding_provider,
            meta.embedding_model,
            meta.embedding_dim,
            meta.pipeline_hash,
            meta.indexed_at,
            meta.git_head,
        ),
    )
    connection.commit()


def read_index_metadata(connection: sqlite3.Connection) -> IndexMetadata | None:
    """Return the stored :class:`IndexMetadata`, or ``None`` for a pre-v11 database."""
    row = connection.execute(
        "SELECT project_name, project_root, embedding_provider, embedding_model, "
        "embedding_dim, pipeline_hash, indexed_at, git_head FROM index_metadata WHERE id=1"
    ).fetchone()
    if row is None:
        return None
    return IndexMetadata(
        project_name=row["project_name"] or "",
        project_root=row["project_root"] or "",
        embedding_provider=row["embedding_provider"] or "",
        embedding_model=row["embedding_model"] or "",
        embedding_dim=row["embedding_dim"] if row["embedding_dim"] is not None else -1,
        pipeline_hash=row["pipeline_hash"] or "",
        indexed_at=row["indexed_at"] or 0.0,
        git_head=row["git_head"] or "",
    )
