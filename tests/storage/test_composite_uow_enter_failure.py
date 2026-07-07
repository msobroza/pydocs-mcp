"""CompositeUnitOfWork.__aenter__ must unwind already-entered children.

Python never calls ``__aexit__`` when ``__aenter__`` raises, so when a later
child's enter fails (a corrupt ``.tq`` sidecar raising OSError, a missing
``[late-interaction]`` extra raising ImportError), every child entered before
it would stay entered forever. For a ``SqliteUnitOfWork`` child that means
the ``_sqlite_transaction`` ContextVar keeps pointing at an abandoned,
never-committed connection: subsequent repository calls in the same task
silently route their writes onto it (silent data loss) and the BEGIN'd
connection is never released.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.factories import build_connection_provider
from pydocs_mcp.storage.sqlite.transaction import _sqlite_transaction
from pydocs_mcp.storage.sqlite.uow import SqliteUnitOfWork


@dataclass
class _TrackingChild:
    """Child whose enter/exit lifecycle is observable."""

    entered: bool = False
    exited: bool = False

    async def __aenter__(self) -> _TrackingChild:
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.exited = True

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


@dataclass
class _BoomOnEnter:
    """Child whose ``__aenter__`` fails — e.g. a corrupt .tq sidecar."""

    boom_count: int = 1
    attempts: int = 0

    async def __aenter__(self) -> _BoomOnEnter:
        self.attempts += 1
        if self.attempts <= self.boom_count:
            raise OSError("not a TVIM file: wrong magic")
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


@pytest.mark.asyncio
async def test_enter_failure_exits_already_entered_children() -> None:
    first = _TrackingChild()
    with pytest.raises(OSError, match="TVIM"):
        async with CompositeUnitOfWork(first, _BoomOnEnter()):
            pass  # pragma: no cover — enter fails before the body
    assert first.entered is True
    assert first.exited is True


@pytest.mark.asyncio
async def test_enter_failure_clears_ambient_sqlite_transaction(tmp_path: Path) -> None:
    """The real leak: a SqliteUnitOfWork child left entered keeps the
    ``_sqlite_transaction`` ContextVar set, silently rerouting every later
    repository call in this task onto an abandoned connection."""
    db_path = tmp_path / "leak.db"
    open_index_database(db_path).close()
    provider = build_connection_provider(db_path)
    composite = CompositeUnitOfWork(SqliteUnitOfWork(provider=provider), _BoomOnEnter())

    with pytest.raises(OSError, match="TVIM"):
        async with composite:
            pass  # pragma: no cover — enter fails before the body

    assert _sqlite_transaction.get() is None


@pytest.mark.asyncio
async def test_composite_is_reusable_after_enter_failure() -> None:
    """A failed enter must leave the composite in a clean state: a retry
    (e.g. after the corrupt sidecar is repaired) enters every child exactly
    once instead of double-entering the survivors of the failed attempt."""
    first = _TrackingChild()
    flaky = _BoomOnEnter(boom_count=1)
    composite = CompositeUnitOfWork(first, flaky)

    with pytest.raises(OSError, match="TVIM"):
        async with composite:
            pass  # pragma: no cover — enter fails before the body

    async with composite as uow:
        assert uow is composite
    assert flaky.attempts == 2
