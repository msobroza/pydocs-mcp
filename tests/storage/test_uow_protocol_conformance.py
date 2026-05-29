# Conformance + behavior guards for CompositeUnitOfWork <-> UnitOfWork.
#
# The STATIC conformance guard lives in composite_uow.py itself (a
# TYPE_CHECKING `type[UnitOfWork]` assignment) so CI's
# `mypy python/pydocs_mcp` gates it — tests/ is not in mypy's scope.
# This file holds the RUNTIME guard: after __aexit__'s return type
# changed from None to bool, it must still NOT suppress exceptions.

from __future__ import annotations

import pytest

from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork


class _FakeChild:
    # Minimal child UoW: async context-manager + commit/rollback no-ops.
    # Carries none of the dispatch attrs — the composite's attr-map is
    # empty, which is fine for exercising the __aexit__ contract.
    def __init__(self) -> None:
        self.exited = False

    async def __aenter__(self) -> _FakeChild:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.exited = True
        return False

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


async def test_aexit_does_not_suppress_exceptions() -> None:
    child = _FakeChild()
    with pytest.raises(ValueError, match="boom"):
        async with CompositeUnitOfWork(child):
            raise ValueError("boom")
    assert child.exited  # the child's __aexit__ still ran during unwind
