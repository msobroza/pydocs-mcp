"""FastPlaidUnitOfWork lifecycle (open / commit / rollback)."""

from __future__ import annotations

import pytest

from pydocs_mcp.db import build_connection_provider
from pydocs_mcp.storage.fast_plaid_uow import FastPlaidUnitOfWork


@pytest.mark.asyncio
async def test_lifecycle_opens_and_closes_without_io(tmp_path, monkeypatch) -> None:
    """No fast_plaid traffic until add/remove/score is called — keeps the
    optional-extra path lazy."""
    sidecar = tmp_path / "test.plaid"
    db_path = tmp_path / "test.db"
    # We monkeypatch out fast_plaid.search so the test runs without the extra.
    calls: list[str] = []

    class _StubFastPlaid:
        def __init__(self, *a, **kw):
            calls.append(f"init({a}, {kw})")

        def create(self, *a, **kw):
            calls.append("create")

        def update(self, *a, **kw):
            calls.append("update")

        def search(self, *a, **kw):
            calls.append("search")
            return []

        def delete(self, *a, **kw):
            calls.append("delete")

    import pydocs_mcp.storage.fast_plaid_uow as mod

    monkeypatch.setattr(mod, "_FastPlaidCls", _StubFastPlaid, raising=False)

    uow = FastPlaidUnitOfWork(
        sidecar_path=sidecar,
        db_path=db_path,
        pipeline_hash="pipeline-x",
        provider=build_connection_provider(db_path),
        device="cpu",
    )
    async with uow:
        pass  # no work — no fast-plaid traffic
    # Only the constructor fires inside __aenter__'s load.
    assert any(c.startswith("init") for c in calls)
    # No score/update/delete.
    assert "search" not in calls
    assert "update" not in calls
    assert "delete" not in calls


@pytest.mark.asyncio
async def test_rollback_safe_when_no_writes(tmp_path, monkeypatch) -> None:
    import pydocs_mcp.storage.fast_plaid_uow as mod

    monkeypatch.setattr(mod, "_FastPlaidCls", lambda *a, **kw: object(), raising=False)
    uow = FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid",
        db_path=tmp_path / "x.db",
        pipeline_hash="h",
        provider=build_connection_provider(tmp_path / "x.db"),
        device="cpu",
    )
    async with uow:
        await uow.rollback()  # no-op when not dirty


@pytest.mark.asyncio
async def test_late_interaction_extra_missing_raises_actionable(monkeypatch, tmp_path) -> None:
    """Without ``fast_plaid`` installed, ``__aenter__`` raises the
    actionable ImportError."""
    import pydocs_mcp.storage.fast_plaid_uow as mod

    monkeypatch.setattr(mod, "_FastPlaidCls", None, raising=False)
    monkeypatch.setattr(mod, "_FAST_PLAID_IMPORT_ERROR", ImportError("fake"), raising=False)
    uow = FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid",
        db_path=tmp_path / "x.db",
        pipeline_hash="h",
        provider=build_connection_provider(tmp_path / "x.db"),
        device="cpu",
    )
    with pytest.raises(ImportError) as exc:
        async with uow:
            pass
    assert "pydocs-mcp[late-interaction]" in str(exc.value)
