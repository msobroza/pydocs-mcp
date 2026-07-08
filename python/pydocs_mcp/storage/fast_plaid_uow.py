"""FastPlaidUnitOfWork — the multi-vector UoW backend (Decision B REVISED).

Owns a ``fast_plaid.search.FastPlaid`` index handle persisted to a
per-project directory sidecar ``~/.pydocs-mcp/{slug}.plaid/``. SQLite is
the source of truth for ``chunk_id ↔ plaid_doc_id`` mapping via the
``chunk_multi_vector_ids`` table — this UoW reads/writes that table
through a :class:`SqliteChunkMultiVectorRepository` bound to the SAME
:class:`ConnectionProvider` the surrounding :class:`SqliteUnitOfWork`
uses. Because the repository routes through ``_maybe_acquire``, it
reuses the ambient ``_sqlite_transaction`` connection when a composite
UoW is open — so the mapping rows ride that one open write transaction
(no second connection, no ``database is locked`` deadlock) and commit /
roll back atomically with the ``chunks`` writes. The repository NEVER
commits; the owning :class:`SqliteUnitOfWork` drives commit / rollback.

Lazy import: ``fast_plaid`` (Rust extension under the
``[late-interaction]`` extra) is imported only inside ``__aenter__`` so
a default install (no extra) never pays the import cost.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.sidecar_uow import require_entered, rollback_safely
from pydocs_mcp.storage.sqlite import SqliteChunkMultiVectorRepository

logger = logging.getLogger(__name__)

# Module-level slots monkeypatched in tests so we never import fast_plaid
# at module-load time. ``_FastPlaidCls`` is the resolved class (set by
# :func:`_ensure_fast_plaid_imported` on first success); the import-error
# slot caches a prior failure so re-entry doesn't re-raise from a fresh
# ``from fast_plaid.search import FastPlaid`` line. Tests override both
# via ``monkeypatch.setattr`` to exercise the import-missing branch
# without actually uninstalling the extra.
_FastPlaidCls: Any = None
_FAST_PLAID_IMPORT_ERROR: Exception | None = None

# Single source of truth for the actionable install hint surfaced when
# the ``[late-interaction]`` extra is missing. Tested verbatim against
# ``"pydocs-mcp[late-interaction]"`` so a substring match guarantees the
# pip command stays correct across edits.
_INSTALL_HINT = (
    "Late-interaction retrieval requires the 'late-interaction' extra. "
    "Install with: pip install 'pydocs-mcp[late-interaction]' "
    "(pulls pylate + fast-plaid + sentence-transformers + torch; "
    "expect ~1-5 GB depending on CUDA wheel selection)."
)

# fast_plaid.search.FastPlaid writes this file inside the index directory
# on ``.create`` and requires it to already exist before ``.update`` will
# run (raises ``FileNotFoundError`` otherwise) — verified against the
# installed fast_plaid 1.3.0 source. It's our only filesystem-level
# ground truth for "does an index already exist on disk", independent of
# whatever chunk_multi_vector_ids currently says. Internal to fast_plaid
# (not a published constant of that package) — if a future fast_plaid
# release renames it, add_vectors's create/update guard degrades back to
# trusting the SQLite offset alone, not a hard failure.
_FAST_PLAID_INDEX_MARKER = "metadata.json"


def _ensure_fast_plaid_imported() -> None:
    """Resolve ``fast_plaid.search.FastPlaid`` on first call; raise an
    actionable :class:`ImportError` if the optional extra isn't installed.

    Idempotent: subsequent calls short-circuit once ``_FastPlaidCls`` is
    populated. The module-level slot pattern keeps the import out of the
    module-load path so default installs (no ``[late-interaction]``
    extra) never pay the Rust-extension import cost.
    """
    global _FastPlaidCls, _FAST_PLAID_IMPORT_ERROR
    if _FastPlaidCls is not None:
        return
    if _FAST_PLAID_IMPORT_ERROR is not None:
        # Replay the cached failure instead of re-importing — tests rely
        # on this branch to assert the actionable message without
        # actually monkeypatching ``sys.modules``.
        raise ImportError(_INSTALL_HINT) from _FAST_PLAID_IMPORT_ERROR
    try:
        from fast_plaid import search as _search

        _FastPlaidCls = _search.FastPlaid
    except ImportError as e:  # pragma: no cover - exercised when extra is missing
        _FAST_PLAID_IMPORT_ERROR = e
        raise ImportError(_INSTALL_HINT) from e


@dataclass
class FastPlaidUnitOfWork:
    """UoW for the ``fast_plaid`` per-project sidecar directory.

    ``commit`` and ``rollback`` here are flag flips: ``fast_plaid``
    persists each ``.update`` / ``.delete`` to disk immediately, so
    there's no in-memory transaction to flush. The surrounding
    :class:`SqliteUnitOfWork` owns rollback semantics for the
    ``chunk_multi_vector_ids`` mapping table (written via
    :class:`SqliteChunkMultiVectorRepository` over the shared
    ``provider``), so cross-store consistency falls out of the composite
    UoW commit order.

    The ``provider`` MUST be the same :class:`ConnectionProvider` the
    composite's :class:`SqliteUnitOfWork` child uses. ``_maybe_acquire``
    then resolves the ambient ``_sqlite_transaction`` connection, so the
    mapping rows land on the open write transaction instead of opening a
    second connection (the prior ``sqlite3.connect`` path deadlocked).
    """

    sidecar_path: Path
    pipeline_hash: str
    provider: ConnectionProvider
    device: str = "cpu"
    low_memory: bool = False

    _handle: Any | None = field(default=None, init=False)
    _dirty: bool = field(default=False, init=False)
    _entered: bool = field(default=False, init=False)

    @property
    def _mapping(self) -> SqliteChunkMultiVectorRepository:
        """The ``chunk_multi_vector_ids`` mapping repository over the shared provider."""
        return SqliteChunkMultiVectorRepository(provider=self.provider)

    async def __aenter__(self) -> FastPlaidUnitOfWork:
        _ensure_fast_plaid_imported()
        # The sidecar parent must exist before ``FastPlaid`` mmap's its
        # directory layout. ``mkdir(parents=True, exist_ok=True)`` is
        # idempotent so repeated open-without-write cycles are safe.
        self.sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        # FastPlaid's constructor mmap's the index directory; offload to
        # a worker thread per CLAUDE.md §"Async Patterns" so a large
        # index doesn't stall the event loop.
        self._handle = await asyncio.to_thread(
            _FastPlaidCls,
            index=str(self.sidecar_path),
            device=self.device,
            low_memory=self.low_memory,
        )
        self._entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        # Best-effort rollback when exiting under exception with pending
        # writes. ``fast_plaid`` itself has no in-memory transaction
        # state to discard (each ``.update`` lands on disk), so this is
        # mainly the safety net for symmetry with
        # :class:`SqliteUnitOfWork` — and a hook the future
        # ``add_vectors`` / ``remove_vectors`` tasks can extend to undo
        # writes if needed.
        # Trigger condition deliberately differs from TurboQuant's (dirty
        # alone): fast_plaid persists eagerly, so a clean exit needs no
        # undo — see storage/sidecar_uow.py.
        if self._dirty and exc is not None:
            await rollback_safely(self, logger, "FastPlaid")
        self._entered = False
        self._handle = None

    async def commit(self) -> None:
        """Mark the transaction as clean — fast-plaid persists eagerly.

        Each ``.update`` / ``.delete`` call already lands on disk inside
        the sidecar directory, so ``commit`` has nothing to flush. The
        flag flip provides the explicit-success signal that
        :class:`CompositeUnitOfWork` needs for symmetry with
        :class:`SqliteUnitOfWork.commit`.
        """
        self._dirty = False

    async def rollback(self) -> None:
        """Discard the dirty flag — fast-plaid writes are already on disk.

        No partial transaction state exists at this layer: ``fast_plaid``
        persists every write immediately. Rolling back the SQLite-side
        ``chunk_multi_vector_ids`` mapping is the surrounding
        :class:`SqliteUnitOfWork`'s responsibility, and the composite
        UoW's commit ordering keeps the mapping table the source of
        truth (an unmatched plaid_doc_id becomes invisible to queries).
        So our rollback is a flag flip.
        """
        self._dirty = False

    @property
    def multi_vectors(self) -> FastPlaidUnitOfWork:
        """Self-reference so :class:`CompositeUnitOfWork`'s child-scan
        finds the multi-vector store on this child.

        ``FastPlaidUnitOfWork`` IS the :class:`MultiVectorStore` — it
        implements the Protocol directly via ``add_vectors`` /
        ``remove_vectors`` / ``clear_all`` / ``score``. Mirrors the
        ``TurboQuantUnitOfWork.vectors`` precedent for the single-vector
        path: a UoW that wraps a store also exposes itself under the
        repo-attribute name so the composite's ``_DISPATCH_ATTRS`` scan
        routes ``uow.multi_vectors`` to this instance instead of the
        SQLite-side :class:`NullMultiVectorStore` placeholder.
        """
        return self

    async def add_vectors(
        self,
        ids: Sequence[int],
        embeddings: Sequence[Sequence[np.ndarray]],
    ) -> None:
        """Append multi-vector docs to the sidecar and record the mapping.

        Each ``embeddings[i]`` is a list of per-token (``dim``-sized)
        ``np.ndarray`` vectors for chunk ``ids[i]``; we stack them into
        a single ``(n_tokens, dim)`` tensor that ``fast_plaid`` ingests.
        ``plaid_doc_id`` is assigned by appending: the next index after
        ``MAX(plaid_doc_id)`` in ``chunk_multi_vector_ids``. This keeps
        the mapping stable across reindexes — never reused, never
        renumbered — which is what makes ``remove_vectors`` safe.
        """
        handle = require_entered(
            self._handle if self._entered else None,
            "FastPlaidUnitOfWork.add_vectors",
        )
        if not ids:
            return
        # Lazy import torch — keeps the ``[late-interaction]`` extra gated
        # to the write path that actually needs it (the import is ~1-2 GB
        # of native libs we don't want to pay for at module-load time).
        import torch  # type: ignore[import-not-found, unused-ignore]

        doc_tensors = [
            torch.from_numpy(np.stack(emb, axis=0).astype(np.float32, copy=False))
            for emb in embeddings
        ]
        mapping = self._mapping
        # Probe current max plaid_doc_id; gives the offset N for the new
        # batch. ``next_plaid_offset`` returns 0 on the empty-index path
        # so the first batch always starts at 0.
        offset = await mapping.next_plaid_offset()
        # fast-plaid contract (verified against the installed fast_plaid
        # source): ``.create`` unconditionally (re)initializes the index
        # directory — safe to call any time, offset==0 always picks it, and
        # a legitimately-emptied mapping table (e.g. after ``clear_all``,
        # which soft-deletes plaid slots but leaves the directory in place)
        # correctly starts a fresh index this way. ``.update`` instead
        # RAISES unless ``<index>/metadata.json`` already exists on disk —
        # so offset > 0 alone is NOT sufficient to pick ``.update``: a
        # deleted/never-created ``.plaid`` directory with stale mapping
        # rows (sidecar/DB divergence) would otherwise call ``.update``
        # against a nonexistent index and crash. Guard that one case by
        # checking the real on-disk marker before trusting a positive
        # offset; a mismatch means divergence, so recover via ``.create``
        # at a reset offset instead of raising.
        index_exists_on_disk = (self.sidecar_path / _FAST_PLAID_INDEX_MARKER).exists()
        use_update = offset > 0 and index_exists_on_disk
        if offset > 0 and not index_exists_on_disk:
            logger.warning(
                "fast-plaid sidecar divergence detected: chunk_multi_vector_ids "
                "has rows (offset=%d) but %r has no on-disk index. Recovering by "
                "recreating the index — existing plaid_doc_id assignments are stale "
                "and will be reassigned on next reindex.",
                offset,
                str(self.sidecar_path),
            )
            offset = 0
        await asyncio.to_thread(
            handle.update if use_update else handle.create,
            doc_tensors,
        )
        packages = await mapping.packages_for_chunks(ids)
        await mapping.upsert(
            [
                (cid, offset + i, packages.get(cid, ""), self.pipeline_hash)
                for i, cid in enumerate(ids)
            ]
        )
        self._dirty = True

    async def remove_vectors(self, ids: Sequence[int]) -> None:
        """Soft-delete the fast-plaid slots and drop the mapping rows.

        ``fast_plaid.delete(subset=[...])`` leaves the slots in place so
        existing ``plaid_doc_id`` assignments stay stable — the slots
        just stop matching at query time. Mapping-row removal is what
        makes the chunks invisible from the SQL side.
        """
        handle = require_entered(
            self._handle if self._entered else None,
            "FastPlaidUnitOfWork.remove_vectors",
        )
        if not ids:
            return
        # The repository SELECTs the plaid_doc_ids and DELETEs the mapping
        # rows on the ambient connection; we feed those ids to the
        # fast-plaid soft-delete. fast-plaid keeps the slots in place so
        # existing plaid_doc_id assignments stay stable.
        plaid_ids = await self._mapping.delete_by_chunk_ids(ids)
        if plaid_ids:
            await asyncio.to_thread(handle.delete, subset=list(plaid_ids))
        self._dirty = True

    async def clear_all(self) -> None:
        """Wipe every fast-plaid slot and every mapping row.

        After this returns, the late-interaction sidecar holds no live
        vectors and ``chunk_multi_vector_ids`` is empty.
        """
        handle = require_entered(
            self._handle if self._entered else None,
            "FastPlaidUnitOfWork.clear_all",
        )
        plaid_ids = await self._mapping.clear()
        if plaid_ids:
            await asyncio.to_thread(handle.delete, subset=list(plaid_ids))
        self._dirty = True

    async def score(
        self,
        query_embedding: Sequence[np.ndarray],
        *,
        subset_chunk_ids: Sequence[int],
        top_k: int,
    ) -> tuple[tuple[int, float], ...]:
        """Subset-filtered MaxSim score over the fast-plaid index.

        The hexagonal seam between SQLite (FilterAdapter-produced subset)
        and fast-plaid (MaxSim engine). Looks up ``plaid_doc_id``s for the
        SQLite-filtered ``subset_chunk_ids`` via the
        ``chunk_multi_vector_ids`` mapping table, packs the query
        ``MultiVector`` into ``(1, n_q, dim)`` for fast-plaid's batched
        search, then reverse-maps results to ``(chunk_id, score)`` pairs
        so callers stay on the SQLite id space.
        """
        handle = require_entered(
            self._handle if self._entered else None,
            "FastPlaidUnitOfWork.score",
        )
        if not subset_chunk_ids:
            return ()
        # Lazy import torch — keeps the optional-extra gated to the read path.
        import torch  # type: ignore[import-not-found, unused-ignore]

        # ``plaid_ids_for_chunks`` returns ``(plaid_doc_id, chunk_id)`` pairs;
        # build the reverse map so fast-plaid hits map back to chunk_ids.
        mapping = dict(await self._mapping.plaid_ids_for_chunks(subset_chunk_ids))
        if not mapping:
            return ()
        # Pack the query MultiVector into shape (1, n_q, dim) — fast-plaid expects a batch.
        q_stack = np.stack(list(query_embedding), axis=0).astype(np.float32, copy=False)
        q_tensor = torch.from_numpy(q_stack).unsqueeze(0)
        plaid_ids = list(mapping.keys())
        raw = await asyncio.to_thread(
            handle.search,
            queries_embeddings=q_tensor,
            top_k=top_k,
            subset=plaid_ids,
        )
        # raw is list[list[(plaid_doc_id, score)]] — one inner list per query.
        if not raw:
            return ()
        return tuple(
            (mapping[plaid_id], float(score)) for (plaid_id, score) in raw[0] if plaid_id in mapping
        )


__all__ = ("FastPlaidUnitOfWork",)
