"""Tests for LookupService dispatch (sub-PR #6 §6).

Uses MagicMock + AsyncMock for the backing services so each branch can
be exercised in isolation. Real-store integration is covered by the
golden-fixture suite in tests/test_mcp_surface.py.

Post-I9: ``tree_svc`` and ``ref_svc`` are mandatory.  Tests that
previously passed ``tree_svc=None`` / ``ref_svc=None`` to exercise
the no-backing-service path now pass ``NullTreeService()`` /
``NullReferenceService()`` — same user-visible error contract, no
``is None`` branches in production code.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    NotFoundError,
    ServiceUnavailableError,
)
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.application.null_services import (
    NullReferenceService,
    NullTreeService,
)
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


def _null_tree() -> NullTreeService:
    return NullTreeService()


def _null_ref() -> NullReferenceService:
    return NullReferenceService()


# ── Empty target → list packages ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_empty_target_returns_package_list(
    package_lookup_mock: MagicMock,
) -> None:
    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=_null_tree(),
        ref_svc=_null_ref(),
    )
    out = await svc.lookup(LookupInput(target=""))
    assert "fastapi" in out
    assert "0.110.0" in out
    package_lookup_mock.list_packages.assert_awaited_once()


# ── Single-segment target → package doc ──────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_package_only_returns_package_doc(
    package_lookup_mock: MagicMock,
    fake_package: Package,
) -> None:
    doc = PackageDoc(package=fake_package, chunks=(), members=())
    package_lookup_mock.get_package_doc = AsyncMock(return_value=doc)

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=_null_tree(),
        ref_svc=_null_ref(),
    )
    out = await svc.lookup(LookupInput(target="fastapi"))

    assert "fastapi" in out
    assert "A modern web framework" in out
    package_lookup_mock.get_package_doc.assert_awaited_once_with("fastapi")


@pytest.mark.asyncio
async def test_lookup_unknown_package_raises_not_found(
    package_lookup_mock: MagicMock,
) -> None:
    package_lookup_mock.get_package_doc = AsyncMock(return_value=None)
    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=_null_tree(),
        ref_svc=_null_ref(),
    )

    with pytest.raises(NotFoundError) as exc:
        await svc.lookup(LookupInput(target="nonexistent"))
    assert "nonexistent" in str(exc.value)


# ── _longest_indexed_module — tree_svc wiring ────────────────────────────


@pytest.mark.asyncio
async def test_longest_indexed_module_prefers_tree_when_wired(
    package_lookup_mock: MagicMock,
) -> None:
    """``_longest_indexed_module`` probes ``tree_svc.exists`` — the cheap row
    check — instead of deserializing each candidate via ``get_tree``."""
    tree_svc = MagicMock()

    async def _exists(package: str, module: str) -> bool:
        return module == "fastapi.routing"

    tree_svc.exists = _exists

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=_null_ref(),
    )
    match = await svc._longest_indexed_module(
        "fastapi", ["fastapi", "routing", "APIRouter", "include_router"]
    )
    # Returns (module_id, parts_consumed). A1 lookup-UX fix needs the
    # consumed count so caller can slice symbol_path correctly when the
    # matched module carries a synthetic .md/.ipynb suffix.
    assert match == ("fastapi.routing", 2)


@pytest.mark.asyncio
async def test_longest_indexed_module_falls_back_to_find_module(
    package_lookup_mock: MagicMock,
) -> None:
    """With ``NullTreeService`` (no tree index), fall back to
    ``PackageLookup.find_module`` — ``NullTreeService.exists`` returns
    ``False`` so the fallback path runs unchanged."""

    async def _find(package: str, module: str) -> bool:
        return module == "fastapi.routing"

    package_lookup_mock.find_module = AsyncMock(side_effect=_find)

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=_null_tree(),
        ref_svc=_null_ref(),
    )
    match = await svc._longest_indexed_module("fastapi", ["fastapi", "routing", "APIRouter"])
    assert match == ("fastapi.routing", 2)


@pytest.mark.asyncio
async def test_longest_indexed_module_returns_none_when_nothing_matches(
    package_lookup_mock: MagicMock,
) -> None:
    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=_null_tree(),
        ref_svc=_null_ref(),
    )
    match = await svc._longest_indexed_module("fastapi", ["fastapi", "nonexistent", "foo"])
    assert match is None


@pytest.mark.asyncio
async def test_longest_indexed_module_resolves_markdown_via_suffix_probe(
    package_lookup_mock: MagicMock,
) -> None:
    """A1: lookup('pkg.foo') must resolve to a stored 'pkg.foo.md' tree.
    F20 added the .md/.ipynb suffix to doc-file module ids; without
    this fallback probe, every markdown / notebook document becomes
    unreachable via lookup.

    Consumed count must reflect the user's INPUT prefix length (2),
    not the matched module's segment count (3) — the synthetic suffix
    segment isn't in the user's input, so a downstream symbol_path
    slice must use the input-consumed count."""
    tree_svc = MagicMock()

    async def _exists(package: str, module: str) -> bool:
        return module == "docs.guide.md"

    tree_svc.exists = _exists
    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=_null_ref(),
    )
    match = await svc._longest_indexed_module("__project__", ["docs", "guide"])
    assert match == ("docs.guide.md", 2)


@pytest.mark.asyncio
async def test_longest_indexed_module_python_wins_over_markdown(
    package_lookup_mock: MagicMock,
) -> None:
    """A1: when both pkg.foo (Python) and pkg.foo.md exist, the bare
    Python name wins — variant order is ('', '.md', '.ipynb') so the
    bare probe fires first. Matches user intent: typing pkg.foo wants
    the code module, not the sidecar doc."""
    tree_svc = MagicMock()

    async def _exists(package: str, module: str) -> bool:
        return module in {"pkg.foo", "pkg.foo.md"}

    tree_svc.exists = _exists
    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=_null_ref(),
    )
    match = await svc._longest_indexed_module("pkg", ["pkg", "foo"])
    assert match == ("pkg.foo", 2)


# ── Module-level + symbol-level dispatch ─────────────────────────────────


@pytest.mark.asyncio
async def test_module_lookup_with_null_tree_svc_raises_service_unavailable(
    package_lookup_mock: MagicMock,
) -> None:
    """A multi-segment target with ``NullTreeService`` wired: the Null
    impl's ``get_tree`` raises ``ServiceUnavailableError`` directly
    (preserves the pre-refactor user-facing error contract; no ``is
    None`` branch in the dispatcher)."""
    package_lookup_mock.find_module = AsyncMock(return_value=True)
    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=_null_tree(),
        ref_svc=_null_ref(),
    )

    with pytest.raises(ServiceUnavailableError):
        await svc.lookup(LookupInput(target="fastapi.routing"))


@pytest.mark.asyncio
async def test_module_lookup_with_tree_svc_returns_rendered_tree(
    package_lookup_mock: MagicMock,
) -> None:
    fake_tree = MagicMock()
    fake_tree.to_pageindex_json = MagicMock(return_value={"title": "routing", "nodes": []})
    tree_svc = MagicMock()
    tree_svc.exists = AsyncMock(return_value=True)
    tree_svc.get_tree = AsyncMock(return_value=fake_tree)

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=_null_ref(),
    )
    out = await svc.lookup(LookupInput(target="fastapi.routing"))
    assert "routing" in out


def _tree_svc_for_module(module_path: str, tree: Any) -> MagicMock:
    """Build a tree_svc mock that resolves only one exact module path.

    ``_longest_indexed_module`` calls ``exists`` (cheap probe); the winning
    candidate then flows into ``_module_lookup`` / ``_symbol_lookup`` which
    call ``get_tree`` once. Mock both to keep the dispatch path realistic.
    """
    svc = MagicMock()

    async def _exists(package: str, module: str) -> bool:
        return module == module_path

    async def _get_tree(package: str, module: str) -> Any:
        return tree if module == module_path else None

    svc.exists = _exists
    svc.get_tree = _get_tree
    return svc


@pytest.mark.asyncio
async def test_symbol_lookup_not_found_when_module_resolves_but_symbol_missing(
    package_lookup_mock: MagicMock,
) -> None:
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=None)
    tree_svc = _tree_svc_for_module("fastapi.routing", fake_tree)

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=_null_ref(),
    )
    with pytest.raises(NotFoundError):
        await svc.lookup(LookupInput(target="fastapi.routing.NoSuchClass"))


@pytest.mark.asyncio
async def test_show_callers_with_null_ref_svc_raises_service_unavailable(
    package_lookup_mock: MagicMock,
) -> None:
    """``NullReferenceService.callers`` raises ``ServiceUnavailableError``
    pointing at the YAML knob; the dispatcher just propagates it."""
    fake_node = MagicMock()
    fake_node.kind = "method"
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = _tree_svc_for_module("fastapi.routing", fake_tree)

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=_null_ref(),
    )
    with pytest.raises(ServiceUnavailableError):
        await svc.lookup(LookupInput(target="fastapi.routing.X", show="callers"))


@pytest.mark.asyncio
async def test_show_inherits_on_non_class_raises_invalid_argument(
    package_lookup_mock: MagicMock,
) -> None:
    fake_node = MagicMock()
    fake_node.kind = "method"  # not a class
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = _tree_svc_for_module("fastapi.routing", fake_tree)

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=_null_ref(),
    )
    with pytest.raises(InvalidArgumentError) as exc:
        await svc.lookup(LookupInput(target="fastapi.routing.X.y", show="inherits"))
    assert "class" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_show_inherits_on_class_routes_through_ref_svc(
    package_lookup_mock: MagicMock,
) -> None:
    """Post-#5c contract: ``show='inherits'`` for a class routes through
    ``ref_svc.inherits(package, qname)``. The pre-#5c degraded
    path that read ``node.extra_metadata['inherits_from']`` is gone — the
    reference graph is the single source of truth for INHERITS edges
    once ref_svc is wired. (When ref_svc=None, the dispatch raises
    ServiceUnavailableError pointing at the YAML knob — see
    ``test_lookup_service_refs.py``.)"""
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.storage.node_reference import NodeReference

    fake_node = MagicMock()
    fake_node.node_id = "fastapi.routing.X"
    fake_node.kind = "class"
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = _tree_svc_for_module("fastapi.routing", fake_tree)

    base_refs = (
        NodeReference(
            from_package="fastapi",
            from_node_id="fastapi.routing.X",
            to_name="BaseAuth",
            to_node_id="fastapi.security.BaseAuth",
            kind=ReferenceKind.INHERITS,
        ),
        NodeReference(
            from_package="fastapi",
            from_node_id="fastapi.routing.X",
            to_name="Mixin",
            to_node_id=None,  # unresolved — external base
            kind=ReferenceKind.INHERITS,
        ),
    )
    ref_svc = MagicMock()
    ref_svc.inherits = AsyncMock(return_value=base_refs)

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=ref_svc,
    )
    out = await svc.lookup(LookupInput(target="fastapi.routing.X", show="inherits"))
    assert "BaseAuth" in out
    assert "Mixin" in out
    # Routed through the service's inherits method (spec 2026-07-11 §3.4a).
    ref_svc.inherits.assert_awaited_once_with("fastapi", "fastapi.routing.X")


@pytest.mark.asyncio
async def test_show_inherits_on_class_with_no_bases_returns_friendly_message(
    package_lookup_mock: MagicMock,
) -> None:
    """A class with zero INHERITS edges renders the canonical empty-rows
    markdown via ``format_references`` — H1 + ``No bases found.``"""
    from pydocs_mcp.extraction.reference_kind import ReferenceKind

    fake_node = MagicMock()
    fake_node.node_id = "fastapi.routing.X"
    fake_node.kind = "class"
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = _tree_svc_for_module("fastapi.routing", fake_tree)

    ref_svc = MagicMock()
    ref_svc.inherits = AsyncMock(return_value=())

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=ref_svc,
    )
    out = await svc.lookup(LookupInput(target="fastapi.routing.X", show="inherits"))
    assert "No bases found." in out
    ref_svc.inherits.assert_awaited_once_with("fastapi", "fastapi.routing.X")


@pytest.mark.asyncio
async def test_show_callers_with_ref_svc_renders_refs(
    package_lookup_mock: MagicMock,
) -> None:
    """Post-#5c: callers route through ``format_references`` with the real
    ``NodeReference`` shape (the placeholder ``MagicMock`` dance is gone —
    we use the actual value object so the rendered output matches the
    §A.1 contract end users see)."""
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.storage.node_reference import NodeReference

    fake_node = MagicMock()
    fake_node.node_id = "fastapi.routing.X.y"
    fake_node.kind = "method"
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = _tree_svc_for_module("fastapi.routing", fake_tree)

    ref = NodeReference(
        from_package="caller_pkg",
        from_node_id="caller.mod.a",
        to_name="fastapi.routing.X.y",
        to_node_id="fastapi.routing.X.y",
        kind=ReferenceKind.CALLS,
    )
    ref_svc = MagicMock()
    ref_svc.callers = AsyncMock(return_value=(ref,))

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=ref_svc,
    )
    out = await svc.lookup(LookupInput(target="fastapi.routing.X.y", show="callers"))
    assert "caller.mod.a" in out
    assert "fastapi.routing.X.y" in out
    ref_svc.callers.assert_awaited_once_with("fastapi", "fastapi.routing.X.y")


@pytest.mark.asyncio
async def test_show_callees_with_ref_svc_invokes_callees_method(
    package_lookup_mock: MagicMock,
) -> None:
    """Post-#5c: empty callees renders ``# Callees of `qname` \\nNo
    callees found.\\n`` via ``format_references`` — NOT the pre-#5c
    ``(no references)`` placeholder. The 2-arg call assertion remains
    intact (Decision C1 from #5b)."""
    fake_node = MagicMock()
    fake_node.node_id = "fastapi.routing.X.y"
    fake_node.kind = "method"
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = _tree_svc_for_module("fastapi.routing", fake_tree)

    ref_svc = MagicMock()
    ref_svc.callees = AsyncMock(return_value=())

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=ref_svc,
    )
    out = await svc.lookup(LookupInput(target="fastapi.routing.X.y", show="callees"))
    assert out.startswith("# Callees of `fastapi.routing.X.y`\n")
    assert "No callees found." in out
    ref_svc.callees.assert_awaited_once_with("fastapi", "fastapi.routing.X.y")


@pytest.mark.asyncio
async def test_show_tree_on_symbol_returns_node_json(
    package_lookup_mock: MagicMock,
) -> None:
    fake_node = MagicMock()
    fake_node.kind = "class"
    fake_node.to_pageindex_json = MagicMock(
        return_value={"title": "APIRouter", "nodes": [{"title": "include_router"}]}
    )
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = _tree_svc_for_module("fastapi.routing", fake_tree)

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=_null_ref(),
    )
    out = await svc.lookup(LookupInput(target="fastapi.routing.APIRouter", show="tree"))
    assert "APIRouter" in out
    assert "include_router" in out


# ── I8 dispatch table + I9 Null-services contract ────────────────────────


@pytest.mark.asyncio
async def test_symbol_lookup_unknown_show_raises_invalid_argument(
    package_lookup_mock: MagicMock,
) -> None:
    """I8: unknown ``show`` values raise ``InvalidArgumentError`` from
    the dispatch table lookup (replaces the trailing ``raise`` after
    the 6-level nested if/elif).  Guard is a single ``.get`` against
    the table; the test pins it stays in place.

    ``LookupInput`` already validates ``show`` at the schema layer, so
    we invoke ``_symbol_lookup`` directly here — the defence-in-depth
    InvalidArgumentError inside the dispatcher catches the case where
    a future internal caller bypasses the schema (or where the schema's
    Literal grows out of sync with the table)."""
    fake_node = MagicMock()
    fake_node.kind = "method"
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = _tree_svc_for_module("fastapi.routing", fake_tree)

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=_null_ref(),
    )
    with pytest.raises(InvalidArgumentError) as exc:
        await svc._symbol_lookup(
            package="fastapi",
            module="fastapi.routing",
            target="fastapi.routing.X.y",
            show="weird-mode",
            limit=10,
        )
    assert "unknown show" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_symbol_lookup_inherits_class_check_runs_before_ref_call(
    package_lookup_mock: MagicMock,
) -> None:
    """I8: the ``inherits``-only-for-classes guard runs BEFORE the
    dispatch-table call, so a non-class node never hits ``ref_svc``.
    If the guard moves into the table lambda, the Null impl will raise
    ServiceUnavailableError first — masking the real InvalidArgumentError."""
    fake_node = MagicMock()
    fake_node.kind = "function"  # not a class
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = _tree_svc_for_module("pkg.mod", fake_tree)

    ref_svc = MagicMock()
    ref_svc.find_by_name = AsyncMock(return_value=())
    ref_svc.callers = AsyncMock(return_value=())
    ref_svc.callees = AsyncMock(return_value=())

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=ref_svc,
    )
    with pytest.raises(InvalidArgumentError) as exc:
        await svc.lookup(LookupInput(target="pkg.mod.func", show="inherits"))
    assert "class" in str(exc.value).lower()
    # ref_svc was never reached — class-check fires first.
    ref_svc.find_by_name.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_service_accepts_null_tree_and_ref_services(
    package_lookup_mock: MagicMock,
) -> None:
    """I9: ``NullTreeService`` and ``NullReferenceService`` satisfy
    ``LookupService``'s structural contract — construction works, the
    empty-target path returns packages without ever touching the Null
    impls (so they don't raise spuriously on read-only meta paths)."""
    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=_null_tree(),
        ref_svc=_null_ref(),
    )
    # Empty target → list_packages, never invokes tree_svc or ref_svc.
    out = await svc.lookup(LookupInput(target=""))
    assert "fastapi" in out


@pytest.mark.asyncio
async def test_null_ref_svc_callers_raises_yaml_anchored_message(
    package_lookup_mock: MagicMock,
) -> None:
    """I9 + S20: the YAML-anchored failure message
    (``reference_graph.capture.enabled``) is now raised from inside
    ``NullReferenceService`` rather than from a sentinel branch in
    ``_symbol_lookup``.  Same user-visible error contract, but the
    dispatcher stays branch-free."""
    fake_node = MagicMock()
    fake_node.node_id = "pkg.mod.f"
    fake_node.kind = "function"
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = _tree_svc_for_module("pkg.mod", fake_tree)

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=_null_ref(),
    )
    with pytest.raises(ServiceUnavailableError) as exc:
        await svc.lookup(LookupInput(target="pkg.mod.f", show="callers"))
    assert "reference_graph.capture.enabled" in str(exc.value)


@pytest.mark.asyncio
async def test_ref_getters_table_has_expected_keys() -> None:
    """I8: the dispatch table covers exactly the reference-graph show modes —
    callers/callees/inherits plus the §D18 governed_by decisions-as-graph-nodes
    direction.  Pinning the keyset prevents silent drift (e.g. a future
    show='subclasses' added to the input schema without a matching table entry
    would fall through to InvalidArgumentError, which the symmetric test above
    catches)."""
    from pydocs_mcp.application.lookup_service import _REF_GETTERS

    assert set(_REF_GETTERS) == {"callers", "callees", "inherits", "governed_by"}
