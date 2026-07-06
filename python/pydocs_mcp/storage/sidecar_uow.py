"""Shared guard + __aexit__ helpers for sidecar UoW backends.

``TurboQuantUnitOfWork`` (.tq single-vector sidecar) and
``FastPlaidUnitOfWork`` (.plaid multi-vector sidecar) previously
hand-copied two contracts that drifted: the 'used outside async with'
guard (plain RuntimeError vs :class:`UnitOfWorkNotEnteredError`) and the
best-effort __aexit__ rollback with a WARNING log. This module is the
single source of truth for both, so the next sidecar backend inherits
the contract instead of re-copying it.

The differing rollback TRIGGER conditions stay at the __aexit__ call
sites (TurboQuant: ``if self._dirty``; FastPlaid: ``if self._dirty and
exc is not None``) — that asymmetry is deliberate, as are the genuinely
different commit/rollback semantics (atomic-rename flush vs flag flip),
which is why there is NO common base class here.
"""

from __future__ import annotations

import logging
from typing import Protocol, TypeVar

from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError

T = TypeVar("T")


class SupportsRollback(Protocol):
    """Structural view of a UoW as far as ``rollback_safely`` cares."""

    async def rollback(self) -> None: ...


def require_entered(handle: T | None, op: str) -> T:
    """Return ``handle``; raise :class:`UnitOfWorkNotEnteredError` when None.

    Returning the narrowed value (instead of a boolean check) lets mypy
    collapse ``X | None`` to ``X`` at the call site::

        index = require_entered(self._index, "TurboQuantUnitOfWork.add_vectors")

    ``op`` MUST be a bare ``Class.method`` label, never a sentence —
    ``UnitOfWorkNotEnteredError.__init__`` templates it into its own
    'accessed outside async with' message.
    """
    if handle is None:
        raise UnitOfWorkNotEnteredError(op)
    return handle


async def rollback_safely(
    uow: SupportsRollback,
    log: logging.Logger,
    label: str,
) -> None:
    """Best-effort __aexit__ rollback: never mask the original exception.

    A rollback failure is logged at WARNING (an operator running at
    WARNING+ still sees it) and swallowed, so the exception that
    triggered ``__aexit__`` propagates unchanged.
    """
    try:
        await uow.rollback()
    except Exception as rollback_exc:
        log.warning("%s rollback in __aexit__ failed: %r", label, rollback_exc)


__all__ = ("SupportsRollback", "require_entered", "rollback_safely")
