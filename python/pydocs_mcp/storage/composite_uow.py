"""CompositeUnitOfWork — best-effort coordinator over N child UoWs (spec §5.5).

For this PR there are exactly two children:
- SqliteUnitOfWork (packages, chunks, module_members, trees, references)
- TurboQuantUnitOfWork (vectors — the .tq sidecar)

Commit semantics: each child commits sequentially. On any failure,
already-committed children get rollback() called (best-effort —
SQLite cannot un-commit, but TurboQuant can reload its pre-commit
on-disk state). The original exception is re-raised so the caller
sees the failure.

Atomicity limitation: NOT strict cross-backend ACID. The startup
integrity check (compare chunks.count to IdMapIndex.size()) detects
post-crash mismatches and forces re-embed of affected packages.
"""
from __future__ import annotations

import logging
from typing import Any

from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError

logger = logging.getLogger(__name__)


# Performance: scanning a fixed canonical attribute set once (at
# __init__ or __aenter__) avoids re-walking every child's `hasattr`
# surface on every attribute read. New repositories on the UnitOfWork
# Protocol must be added to this tuple.
_DISPATCH_ATTRS = (
    "packages",
    "chunks",
    "module_members",
    "trees",
    "references",
    "vectors",
)


class CompositeUnitOfWork:
    """Best-effort coordinator over N child UoWs (spec §5.5)."""

    def __init__(self, *children: Any) -> None:
        if not children:
            raise ValueError(
                "CompositeUnitOfWork requires at least one child UoW",
            )
        self._children: tuple[Any, ...] = children
        self._entered: list[Any] = []
        # Eager scan: catches class-level / dataclass-field ambiguity at
        # construction. Children whose repos are properties bound only
        # inside __aenter__ (e.g. SqliteUnitOfWork) are scanned again
        # after __aenter__ runs — the second pass fills the deferred
        # owners in and re-checks ambiguity.
        self._attr_map: dict[str, Any] = self._build_attr_map()

    def _build_attr_map(self) -> dict[str, Any]:
        """Scan children for dispatch attrs; detect ambiguity (spec I4 + S26).

        Spec S15: a :class:`NullVectorStore` placeholder must not win
        over a real backend exposing the same attribute. Placeholders
        are filtered before the ambiguity check so a
        ``[SqliteUoW(vectors=Null), TurboQuantUoW]`` composite routes
        ``uow.vectors`` to TurboQuant without tripping the multi-owner
        guard. Placeholders ARE remembered as a last-resort fallback so
        the silent-no-op semantics still hold when every child is null.

        Properties that raise :class:`UnitOfWorkNotEnteredError` before
        ``__aenter__`` are tolerated silently — the post-aenter rescan
        fills them in. Any other exception type propagates (real bug).
        """
        # Local import — top-level would create a hard cycle between
        # composite_uow.py and null_vector_store.py (sqlite.py imports
        # this module for the SqliteUnitOfWork.vectors default).
        from pydocs_mcp.storage.null_vector_store import NullVectorStore

        attr_map: dict[str, Any] = {}
        null_fallback: dict[str, Any] = {}
        seen: set[str] = set()
        ambiguous: set[str] = set()
        for child in self._children:
            for attr in _DISPATCH_ATTRS:
                try:
                    if not hasattr(child, attr):
                        continue
                    value = getattr(child, attr)
                except UnitOfWorkNotEnteredError:
                    # The child owns this attr but its underlying repo
                    # is bound only inside __aenter__. Skip silently —
                    # the post-aenter rescan will pick it up.
                    continue
                if isinstance(value, NullVectorStore):
                    null_fallback.setdefault(attr, value)
                    continue
                if attr in seen:
                    ambiguous.add(attr)
                else:
                    attr_map[attr] = value
                    seen.add(attr)
        if ambiguous:
            raise ValueError(
                f"CompositeUnitOfWork has ambiguous attrs across children: "
                f"{sorted(ambiguous)}. Each repository name must be "
                f"unique across children.",
            )
        for attr, value in null_fallback.items():
            attr_map.setdefault(attr, value)
        return attr_map

    async def __aenter__(self) -> "CompositeUnitOfWork":
        for child in self._children:
            await child.__aenter__()
            self._entered.append(child)
        # Rescan: SqliteUnitOfWork-shaped children expose their repos
        # only after __aenter__ runs. The eager __init__ pass skipped
        # them; rebuild the map now that they're bindable so attribute
        # lookups inside the `async with` block resolve in O(1).
        self._attr_map = self._build_attr_map()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        for child in reversed(self._entered):
            try:
                await child.__aexit__(exc_type, exc, tb)
            except Exception as inner_exc:
                logger.warning(
                    "CompositeUnitOfWork child __aexit__ raised: %r",
                    inner_exc,
                )

    async def commit(self) -> None:
        committed: list = []
        first_exc: BaseException | None = None
        for child in self._children:
            try:
                await child.commit()
                committed.append(child)
            except BaseException as exc:
                first_exc = exc
                logger.error(
                    "CompositeUnitOfWork commit failed on %r: %r",
                    child, exc,
                )
                break
        if first_exc is not None:
            for child in reversed(committed):
                try:
                    await child.rollback()
                except Exception as rb_exc:
                    logger.warning(
                        "Best-effort rollback raised on %r: %r — original "
                        "commit failure NOT masked.",
                        child, rb_exc,
                    )
            raise first_exc

    async def rollback(self) -> None:
        for child in reversed(self._children):
            try:
                await child.rollback()
            except Exception as exc:
                logger.warning(
                    "CompositeUnitOfWork.rollback raised on %r: %r",
                    child, exc,
                )

    async def delete_all(self) -> None:
        """Fan-out :meth:`UnitOfWork.delete_all` to every child (spec I3).

        Each child wipes its own backend; per-child failures DO NOT
        short-circuit (best-effort across the composite, mirroring the
        :meth:`rollback` semantics above). Required because attribute
        delegation through :meth:`__getattr__` would only invoke the
        FIRST owning child's ``delete_all`` — leaving the other
        backend's rows behind.
        """
        for child in self._children:
            if not hasattr(child, "delete_all"):
                continue
            await child.delete_all()

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access via the cached owner map (spec S26).

        Ambiguous attributes are caught at construction or __aenter__
        (see :meth:`_build_attr_map`), so this lookup is O(1) and free
        of per-call ``hasattr`` scans. Unknown attributes raise the
        same AttributeError shape as Python's default to keep
        duck-typing and ``getattr(obj, name, default)`` callers
        compatible.
        """
        # Guard against pickling / deepcopy paths where __getattr__
        # fires before __init__ has set _attr_map.
        try:
            attr_map = object.__getattribute__(self, "_attr_map")
        except AttributeError as exc:
            raise AttributeError(name) from exc
        try:
            return attr_map[name]
        except KeyError as exc:
            raise AttributeError(
                f"CompositeUnitOfWork: no child exposes attribute {name!r}",
            ) from exc


__all__ = ("CompositeUnitOfWork",)
