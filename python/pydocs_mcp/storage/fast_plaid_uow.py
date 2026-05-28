"""FastPlaidUnitOfWork — the multi-vector UoW backend (Decision B REVISED).

Owns a ``fast_plaid.search.FastPlaid`` index handle persisted to a
per-project directory sidecar ``~/.pydocs-mcp/{slug}.plaid/``. SQLite is
the source of truth for ``chunk_id ↔ plaid_doc_id`` mapping via the
``chunk_multi_vector_ids`` table — this UoW reads/writes that table
through the SAME ``sqlite3.Connection`` the surrounding
:class:`SqliteUnitOfWork` holds, so the mapping commits atomically with
the ``chunks`` writes.

Lazy import: ``fast_plaid`` (Rust extension under the
``[late-interaction]`` extra) is imported only inside ``__aenter__`` so
a default install (no extra) never pays the import cost.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Note: ``UnitOfWorkNotEnteredError`` is intentionally NOT imported here —
# this module currently scaffolds lifecycle only. The follow-up that adds
# ``add_vectors`` / ``remove_vectors`` / ``score`` will introduce the
# guard checks that need it.

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

    Lifecycle-only this task — ``add_vectors`` / ``remove_vectors`` /
    ``score`` / ``clear_all`` land in subsequent tasks. ``commit`` and
    ``rollback`` here are flag flips: ``fast_plaid`` persists each
    ``.update`` / ``.delete`` to disk immediately, so there's no
    in-memory transaction to flush. The surrounding
    :class:`SqliteUnitOfWork` owns rollback semantics for the
    ``chunk_multi_vector_ids`` mapping table, so cross-store consistency
    falls out of the composite UoW commit order.
    """

    sidecar_path: Path
    db_path: Path
    pipeline_hash: str
    device: str = "cpu"
    low_memory: bool = False

    _handle: Any | None = field(default=None, init=False)
    _dirty: bool = field(default=False, init=False)
    _entered: bool = field(default=False, init=False)

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
        if self._dirty and exc is not None:
            try:
                await self.rollback()
            except Exception as rb:  # pragma: no cover - best-effort path
                # Mirror ``TurboQuantUnitOfWork.__aexit__``: log rollback
                # failures at WARNING+ rather than masking the original
                # exception that triggered ``__aexit__``.
                logger.warning("FastPlaid rollback in __aexit__ failed: %r", rb)
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


__all__ = ("FastPlaidUnitOfWork",)
