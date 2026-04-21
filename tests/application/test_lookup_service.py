"""Tests for LookupService dispatch (sub-PR #6 §6).

Uses MagicMock + AsyncMock for the backing services so each branch can
be exercised in isolation. Real-store integration is covered by the
golden-fixture suite in tests/test_mcp_surface.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    NotFoundError,
    ServiceUnavailableError,
)
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.models import Package, PackageDoc, PackageOrigin


@pytest.fixture
def fake_package() -> Package:
    return Package(
        name="fastapi",
        version="0.110.0",
        summary="A modern web framework",
        homepage="https://fastapi.tiangolo.com",
        dependencies=("starlette", "pydantic"),
        content_hash="abc123",
        origin=PackageOrigin.DEPENDENCY,
    )


@pytest.fixture
def package_lookup_mock(fake_package: Package) -> MagicMock:
    m = MagicMock()
    m.list_packages = AsyncMock(return_value=(fake_package,))
    m.get_package_doc = AsyncMock(return_value=None)
    m.find_module = AsyncMock(return_value=False)
    return m


# ── Empty target → list packages ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_empty_target_returns_package_list(
    package_lookup_mock: MagicMock,
) -> None:
    svc = LookupService(package_lookup=package_lookup_mock)
    out = await svc.lookup(LookupInput(target=""))
    assert "fastapi" in out
    assert "0.110.0" in out
    package_lookup_mock.list_packages.assert_awaited_once()


# ── Single-segment target → package doc ──────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_package_only_returns_package_doc(
    package_lookup_mock: MagicMock, fake_package: Package,
) -> None:
    doc = PackageDoc(package=fake_package, chunks=(), members=())
    package_lookup_mock.get_package_doc = AsyncMock(return_value=doc)

    svc = LookupService(package_lookup=package_lookup_mock)
    out = await svc.lookup(LookupInput(target="fastapi"))

    assert "fastapi" in out
    assert "A modern web framework" in out
    package_lookup_mock.get_package_doc.assert_awaited_once_with("fastapi")


@pytest.mark.asyncio
async def test_lookup_unknown_package_raises_not_found(
    package_lookup_mock: MagicMock,
) -> None:
    package_lookup_mock.get_package_doc = AsyncMock(return_value=None)
    svc = LookupService(package_lookup=package_lookup_mock)

    with pytest.raises(NotFoundError) as exc:
        await svc.lookup(LookupInput(target="nonexistent"))
    assert "nonexistent" in str(exc.value)
