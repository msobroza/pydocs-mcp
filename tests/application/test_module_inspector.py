"""Tests for ModuleInspector — live importlib + inspect (spec §5.1).

Post-#5a-2: the inspector depends only on a ``uow_factory`` — package
existence is checked via ``async with uow_factory() as uow: await
uow.packages.get(name)``. We use the canonical ``InMemoryPackageStore``
from :mod:`tests._fakes` together with ``make_fake_uow_factory`` so the
inspector exercises the genuine UoW shape end-to-end while staying
hermetic. Introspection runs against real stdlib modules (``json``,
``asyncio``) — that keeps the genuine ``importlib`` + ``inspect`` path
under test without needing a real package store.
"""

from __future__ import annotations

import re

import pytest

from pydocs_mcp.application.module_inspector import ModuleInspector
from pydocs_mcp.models import Package, PackageOrigin
from tests._fakes import InMemoryPackageStore, make_fake_uow_factory


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


def _service(
    packages: dict[str, Package] | None = None,
) -> tuple[
    ModuleInspector,
    InMemoryPackageStore,
]:
    store = InMemoryPackageStore(items=dict(packages or {}))
    svc = ModuleInspector(uow_factory=make_fake_uow_factory(packages=store))
    return svc, store


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inspect_unindexed_package() -> None:
    svc, store = _service(packages={})

    result = await svc.inspect("ghost")

    assert result == ("'ghost' is not indexed. Use lookup(target='') to see available packages.")
    assert sum(1 for c in store.calls if c.method == "get") == 1


@pytest.mark.asyncio
async def test_inspect_invalid_submodule() -> None:
    svc, _ = _service(packages={"json": _pkg("json")})

    bad = "';--"
    result = await svc.inspect("json", bad)

    # Mirror the production f-string exactly: the value is wrapped in
    # literal single quotes — no escaping, since the input itself
    # contains a quote.
    expected = f"Invalid submodule '{bad}'. Use only letters, digits, underscores, and dots."
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
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        svc.uow_factory = lambda: None  # type: ignore[misc]
    assert not hasattr(svc, "__dict__")


@pytest.mark.asyncio
async def test_inspect_opens_uow_for_package_lookup() -> None:
    """spec §3.1 — inspect opens a UoW and reads packages through uow.packages.get."""
    svc, store = _service(packages={"json": _pkg("json")})
    # No exception path; the test just exercises the new shape.
    result = await svc.inspect("json")
    assert result.startswith("# json")
    # The uow.packages.get call lands in the InMemory store's .calls history.
    assert any(c.method == "get" and c.payload == "json" for c in store.calls)


# ── Exception narrowing (I10) ──────────────────────────────────────────────
#
# The two ``except Exception`` sites in ``module_inspector._inspect_target``
# protect against custom-DSL libraries that raise unusual types during
# ``inspect.getmembers`` / ``pkgutil.iter_modules``. The narrowed set
# ``(AttributeError, ImportError, OSError, RuntimeError)`` keeps that
# defensive behaviour for the failure modes that actually happen in the
# wild (broken ``__getattr__``, lazy-import shims, FS-backed descriptors,
# RuntimeError-raising property guards) while letting real programming
# bugs — ``ValueError``, ``KeyError``, ``TypeError`` — propagate so they
# surface in tests instead of being silently swallowed.
#
# ``math`` is a single-file C-extension module with no ``__path__``, so
# the ``pkgutil.iter_modules`` fallback at site #2 doesn't trigger. That
# isolates the test to the site #1 ``except`` around the ``getmembers``
# loop — patching ``inspect.getmembers`` is the only failure source on
# the inspect path for that target.


@pytest.mark.asyncio
async def test_inspect_skips_attribute_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """AttributeError from inspect.getmembers → empty member list, no crash."""
    svc, _ = _service(packages={"math": _pkg("math")})

    def _boom(_mod: object) -> list[tuple[str, object]]:
        raise AttributeError("synthetic")

    monkeypatch.setattr("inspect.getmembers", _boom)

    result = await svc.inspect("math")

    # ``math`` has no ``__path__`` (single-file module, not a package), so
    # the iter_modules fallback doesn't trigger. The narrowed except
    # swallows the AttributeError and we land on the "No API in ..."
    # sentinel.
    assert result == "No API in 'math'."


@pytest.mark.asyncio
async def test_inspect_skips_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """ImportError from inspect.getmembers (e.g., lazy-import shims) → empty list."""
    svc, _ = _service(packages={"math": _pkg("math")})

    def _boom(_mod: object) -> list[tuple[str, object]]:
        raise ImportError("synthetic")

    monkeypatch.setattr("inspect.getmembers", _boom)

    result = await svc.inspect("math")

    assert result == "No API in 'math'."


@pytest.mark.asyncio
async def test_inspect_does_not_swallow_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ValueError is NOT in the narrowed set — it must propagate to the caller."""
    svc, _ = _service(packages={"math": _pkg("math")})

    def _boom(_mod: object) -> list[tuple[str, object]]:
        raise ValueError("real bug")

    monkeypatch.setattr("inspect.getmembers", _boom)

    with pytest.raises(ValueError, match="real bug"):
        await svc.inspect("math")
