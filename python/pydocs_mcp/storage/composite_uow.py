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
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)


class CompositeUnitOfWork:
    """Best-effort coordinator over N child UoWs (spec §5.5)."""

    def __init__(self, children: Sequence) -> None:
        if not children:
            raise ValueError(
                "CompositeUnitOfWork requires at least one child UoW",
            )
        self._children = list(children)
        self._entered: list = []

    async def __aenter__(self) -> "CompositeUnitOfWork":
        for child in self._children:
            await child.__aenter__()
            self._entered.append(child)
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

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to whichever child declares it."""
        owners = []
        for child in self._children:
            if hasattr(child, name):
                owners.append(child)
        if not owners:
            raise AttributeError(
                f"CompositeUnitOfWork: no child exposes attribute "
                f"{name!r}",
            )
        if len(owners) > 1:
            raise AttributeError(
                f"CompositeUnitOfWork: attribute {name!r} is ambiguous "
                f"({len(owners)} children expose it). Each repository "
                f"name must be unique across children.",
            )
        return getattr(owners[0], name)


__all__ = ("CompositeUnitOfWork",)
