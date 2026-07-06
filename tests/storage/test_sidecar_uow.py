"""require_entered + rollback_safely — the shared sidecar-UoW helpers.

These two helpers are the single source of truth for (a) the
'UoW used outside async with' guard and (b) the best-effort __aexit__
rollback-with-warning, previously hand-copied between
TurboQuantUnitOfWork and FastPlaidUnitOfWork.
"""

from __future__ import annotations

import logging

import pytest

from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
from pydocs_mcp.storage.sidecar_uow import require_entered, rollback_safely


def test_require_entered_returns_the_handle_for_mypy_narrowing() -> None:
    sentinel = object()
    assert require_entered(sentinel, "SomeUow.op") is sentinel


def test_require_entered_raises_with_bare_op_label() -> None:
    with pytest.raises(UnitOfWorkNotEnteredError) as exc_info:
        require_entered(None, "TurboQuantUnitOfWork.add_vectors")
    # attr_name must be the BARE label — UnitOfWorkNotEnteredError templates
    # it into its own message, so a sentence here would read redundantly.
    assert exc_info.value.attr_name == "TurboQuantUnitOfWork.add_vectors"
    assert "TurboQuantUnitOfWork.add_vectors" in str(exc_info.value)


def test_require_entered_error_is_a_runtimeerror() -> None:
    # Narrowing-compatible with callers that still catch RuntimeError.
    with pytest.raises(RuntimeError):
        require_entered(None, "SomeUow.op")


async def test_rollback_safely_delegates_to_rollback() -> None:
    class _Uow:
        rolled = False

        async def rollback(self) -> None:
            self.rolled = True

    uow = _Uow()
    await rollback_safely(uow, logging.getLogger("test-sidecar"), "TurboQuant")
    assert uow.rolled is True


async def test_rollback_safely_swallows_failure_and_warns(caplog) -> None:
    class _Boom:
        async def rollback(self) -> None:
            raise OSError("disk full")

    with caplog.at_level(logging.WARNING, logger="test-sidecar"):
        await rollback_safely(_Boom(), logging.getLogger("test-sidecar"), "FastPlaid")
    assert "FastPlaid rollback in __aexit__ failed" in caplog.text
    assert "disk full" in caplog.text
