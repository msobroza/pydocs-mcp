"""Tests for ModuleIntrospectionService — live importlib + inspect (spec §5.1).

Uses a local in-memory ``FakePackageStore`` (same pattern as Tasks 3–5). The
service only touches the ``PackageStore`` Protocol plus the standard library,
so we exercise introspection against real stdlib modules (``json``,
``asyncio``) — that keeps the tests hermetic while guaranteeing we exercise
the genuine ``importlib`` + ``inspect`` path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pytest

from pydocs_mcp.application.module_introspection_service import (
    ModuleIntrospectionService,
)
from pydocs_mcp.models import Package, PackageOrigin


# ── Helpers ────────────────────────────────────────────────────────────────


def _pkg(name: str) -> Package:
    return Package(
        name=name,
        version="1.0.0",
        summary=f"{name} summary",
        homepage="",
        dependencies=(),
        content_hash="deadbeef",
        origin=PackageOrigin.DEPENDENCY,
    )


@dataclass
class FakePackageStore:
    packages: dict[str, Package] = field(default_factory=dict)
    get_call_count: int = 0

    async def get(self, name: str) -> Package | None:
        self.get_call_count += 1
        return self.packages.get(name)

    async def list(self, **kwargs: Any) -> list[Package]:  # noqa: ARG002
        return list(self.packages.values())

    async def upsert(self, package: Package) -> None:
        self.packages[package.name] = package

    async def delete(self, filter: Any) -> int:  # noqa: ARG002
        return 0

    async def count(self, filter: Any = None) -> int:  # noqa: ARG002
        return len(self.packages)


def _service(packages: dict[str, Package] | None = None) -> tuple[
    ModuleIntrospectionService, FakePackageStore,
]:
    store = FakePackageStore(packages=dict(packages or {}))
    svc = ModuleIntrospectionService(package_store=store)
    return svc, store


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inspect_unindexed_package() -> None:
    svc, store = _service(packages={})

    result = await svc.inspect("ghost")

    assert result == (
        "'ghost' is not indexed. "
        "Use list_packages() to see available packages."
    )
    assert store.get_call_count == 1


@pytest.mark.asyncio
async def test_inspect_invalid_submodule() -> None:
    svc, _ = _service(packages={"json": _pkg("json")})

    bad = "';--"
    result = await svc.inspect("json", bad)

    # Mirror the production f-string exactly: the value is wrapped in
    # literal single quotes — no escaping, since the input itself
    # contains a quote.
    expected = (
        f"Invalid submodule '{bad}'. "
        "Use only letters, digits, underscores, and dots."
    )
    assert result == expected


@pytest.mark.asyncio
async def test_inspect_successful_root() -> None:
    # ``json`` is always available in the standard library and exposes a small,
    # stable public surface (dump / dumps / load / loads + JSONEncoder /
    # JSONDecoder). We assert on shape only — not exact text — because docs
    # drift between Python minor versions.
    svc, _ = _service(packages={"json": _pkg("json")})

    result = await svc.inspect("json")

    assert result.startswith("# json\n\n")
    # At least one class and one function should appear.
    assert re.search(r"^class JSONEncoder", result, re.MULTILINE)
    assert re.search(r"^def dumps", result, re.MULTILINE)


@pytest.mark.asyncio
async def test_inspect_successful_submodule() -> None:
    # ``asyncio.events`` exports ``AbstractEventLoop`` and
    # ``get_event_loop_policy``. Again we match on structure.
    svc, _ = _service(packages={"asyncio": _pkg("asyncio")})

    result = await svc.inspect("asyncio", "events")

    assert result.startswith("# asyncio.events\n\n")
    assert "class AbstractEventLoop" in result


@pytest.mark.asyncio
async def test_inspect_importerror() -> None:
    svc, _ = _service(packages={"json": _pkg("json")})

    result = await svc.inspect("json", "does_not_exist")

    assert result == "Cannot import 'json.does_not_exist'."


@pytest.mark.asyncio
async def test_inspect_normalizes_package_name() -> None:
    # PyPI-style name with dashes + case must resolve to the lowercase/
    # underscore form used in the DB.
    svc, store = _service(packages={"scikit_learn": _pkg("scikit_learn")})

    # Store lookup succeeds only if we normalise "Scikit-Learn" correctly;
    # the import itself will fail (scikit_learn may not be installed) and
    # produce the "Cannot import" message — that's fine, the assertion is
    # just that we got past the "not indexed" short-circuit.
    result = await svc.inspect("Scikit-Learn")

    assert "is not indexed" not in result


def test_service_is_frozen_slotted_dataclass() -> None:
    svc, _ = _service()

    with pytest.raises((AttributeError, Exception)):
        svc.package_store = None  # type: ignore[misc]
    assert not hasattr(svc, "__dict__")
