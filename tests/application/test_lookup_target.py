"""Tests for :class:`LookupTarget` — value-object parsing of ``lookup`` targets.

Spec I1: extract target-string parsing into a frozen value object so
``LookupService.lookup`` becomes a thin dispatcher over the parsed
shape.  ``LookupTarget.parse`` is the canonical entry point — it takes
a dotted target string + an async ``longest_module`` callback that
resolves the longest indexed module prefix, then returns a frozen
``LookupTarget`` describing what the caller asked for:

- ``package`` — first segment, or ``None`` for the empty target
- ``module`` — full module id (with synthetic ``.md`` / ``.ipynb``
  suffix when applicable), or ``None`` when only a package was named
- ``consumed`` — count of INPUT dotted-parts the module match consumed
  (NOT ``len(module.split("."))`` — see the suffix-probe rationale in
  ``LookupService._longest_indexed_module``)
- ``symbol_path`` — remaining input parts after the module match

The callback shape mirrors ``_longest_indexed_module``: it returns
``(module_id, consumed) | None``.  We thread it in rather than coupling
the value object to ``LookupService`` so the parse logic stays
testable in isolation.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.application.lookup_service import LookupTarget


# ── Empty target ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_empty_target_returns_empty_value_object() -> None:
    """Empty target → package=None, module=None.  Downstream dispatch
    reads this as "list all indexed packages"."""

    async def longest_module(_pkg, _parts):  # pragma: no cover — never called
        return None

    t = await LookupTarget.parse("", longest_module=longest_module)
    assert t.package is None
    assert t.module is None
    assert t.consumed == 0
    assert t.symbol_path == ()


# ── Single-segment target → package only ─────────────────────────────────


@pytest.mark.asyncio
async def test_parse_package_only_does_not_invoke_longest_module() -> None:
    """``len(parts) == 1`` is a package-overview request; no module probe
    is needed.  The callback must NOT fire — calling it would force a
    backend probe just to discover what we already know syntactically."""
    invoked = False

    async def longest_module(_pkg, _parts):
        nonlocal invoked
        invoked = True

    t = await LookupTarget.parse("fastapi", longest_module=longest_module)
    assert t.package == "fastapi"
    assert t.module is None
    assert t.consumed == 1
    assert t.symbol_path == ()
    assert invoked is False


# ── Module + symbol target ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_module_symbol_splits_symbol_path() -> None:
    """``fastapi.routing.APIRouter.include_router`` with a module match
    for ``fastapi.routing`` (2 input parts) leaves ``APIRouter,
    include_router`` as the symbol path."""

    async def longest_module(pkg, parts):
        # Mirror _longest_indexed_module's return shape: (module_id, n_consumed).
        assert pkg == "fastapi"
        assert parts == ("fastapi", "routing", "APIRouter", "include_router")
        return ("fastapi.routing", 2)

    t = await LookupTarget.parse(
        "fastapi.routing.APIRouter.include_router",
        longest_module=longest_module,
    )
    assert t.package == "fastapi"
    assert t.module == "fastapi.routing"
    assert t.consumed == 2
    assert t.symbol_path == ("APIRouter", "include_router")


@pytest.mark.asyncio
async def test_parse_module_only_no_symbol_path() -> None:
    """``fastapi.routing`` resolves to a module with no trailing symbol
    parts → empty ``symbol_path``."""

    async def longest_module(_pkg, _parts):
        return ("fastapi.routing", 2)

    t = await LookupTarget.parse("fastapi.routing", longest_module=longest_module)
    assert t.package == "fastapi"
    assert t.module == "fastapi.routing"
    assert t.consumed == 2
    assert t.symbol_path == ()


# ── No module match → degraded shape ────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_no_module_match_keeps_package_and_clears_module() -> None:
    """Multi-segment target whose module probe returns None: the caller
    (LookupService) raises ``NotFoundError`` with the user's original
    target string.  ``LookupTarget`` itself just returns the no-match
    shape so the dispatcher can branch on it."""

    async def longest_module(pkg, parts):
        return None

    t = await LookupTarget.parse(
        "fastapi.unknown_module.thing",
        longest_module=longest_module,
    )
    assert t.package == "fastapi"
    assert t.module is None
    # consumed stays at 1 (the package itself) so callers can distinguish
    # "package only" (consumed=1, len(parts)==1) from "no module match"
    # (consumed=1, len(parts)>1) via the symbol_path remainder.
    assert t.consumed == 1
    # Everything after the package is "lost" — the caller decides how to
    # surface the unknown-module condition; ``LookupTarget`` doesn't
    # second-guess by stuffing it into symbol_path.
    assert t.symbol_path == ()


# ── Suffix-probe case (markdown / notebook trees) ────────────────────────


@pytest.mark.asyncio
async def test_parse_preserves_synthetic_suffix_in_module_id() -> None:
    """A1 (sub-PR #5 F20) — doc/notebook trees carry a synthetic ``.md``
    / ``.ipynb`` suffix in their module id.  The callback returns the
    full id WITH the suffix; ``consumed`` reflects the user's input
    parts (NOT ``len(module.split("."))``), so a downstream symbol-path
    slice doesn't discard a trailing symbol part the user typed."""

    async def longest_module(_pkg, _parts):
        # User typed two parts: "docs", "guide".  The matched module id
        # is "docs.guide.md" (3 dotted segments after suffix) but
        # ``consumed`` is the input count (2), not the segment count.
        return ("docs.guide.md", 2)

    t = await LookupTarget.parse("docs.guide", longest_module=longest_module)
    assert t.package == "docs"
    assert t.module == "docs.guide.md"
    assert t.consumed == 2
    assert t.symbol_path == ()


# ── Frozen contract ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_target_is_frozen_and_hashable() -> None:
    """``LookupTarget`` is a value object — frozen dataclass with slots.
    Hashable so callers can use it as a dict key for caching or
    deduplication."""

    async def longest_module(_pkg, _parts):
        return ("fastapi.routing", 2)

    t = await LookupTarget.parse(
        "fastapi.routing.X",
        longest_module=longest_module,
    )
    # Frozen — assignment raises AttributeError.
    with pytest.raises(AttributeError):
        t.package = "different"  # type: ignore[misc]
    # Hashable — value-based equality + hash.
    assert hash(t) == hash(t)
