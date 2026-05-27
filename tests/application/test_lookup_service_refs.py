"""Tests for LookupService.ref_svc wire-in (sub-PR #5c, Task 7).

#5b shipped ``ReferenceService`` but left ``LookupService._symbol_lookup``
raising ``ServiceUnavailableError`` for ``show in ("callers","callees",
"inherits")``. This task flips the wire so the dispatch actually invokes
``ref_svc`` and renders through ``application.formatting.format_references``.

The 5 tests below pin:
  1. 2-arg ``ref_svc.callers(package, qname)`` call (Decision C1 from #5b).
  2. 2-arg ``ref_svc.callees(package, qname)`` call.
  3. ``show="inherits"`` routes through ``ref_svc.find_by_name(qname,
     kind=INHERITS)`` (cross-package, name-based — no class-only check
     since ref_svc has visibility into INHERITS edges regardless of node
     kind we can resolve locally).
  4. Empty rows still render the canonical ``# {Verb} of `target` \\nNo
     {noun}s found.\\n`` markdown via ``format_references`` — NOT the
     pre-#5c ``(no references)`` placeholder.
  5. ``NullReferenceService`` raises with the YAML-anchored error
     message (``reference_graph.capture.enabled``) — the old ``sub-PR
     #5b`` text is gone; users get an actionable pointer at the config
     knob.  Post-I9 the Null impl replaces ``ref_svc=None``; the
     user-visible error contract is preserved.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.application.null_services import NullReferenceService
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference


def _tree_svc_for_module(module_path: str, tree):
    """Build a tree_svc that resolves only one module — mirrors the helper
    in ``tests/application/test_lookup_service.py`` so this file stays
    self-contained without cross-test imports."""
    svc = MagicMock()

    async def _exists(package: str, module: str) -> bool:
        return module == module_path

    async def _get_tree(package: str, module: str):
        return tree if module == module_path else None

    svc.exists = _exists
    svc.get_tree = _get_tree
    return svc


def _pkg_lookup_mock() -> MagicMock:
    m = MagicMock()
    m.list_packages = AsyncMock(return_value=())
    m.get_package_doc = AsyncMock(return_value=None)
    m.find_module = AsyncMock(return_value=False)
    return m


def _real_node_tree(qname: str, kind: str = "function"):
    """Tree returning a node with concrete ``node_id`` (we use the qname
    itself as node_id since LookupService passes ``node.node_id`` to
    ref_svc — verifying that argument flow is part of the contract)."""
    node = MagicMock()
    node.node_id = qname
    node.kind = kind
    node.extra_metadata = {}
    tree = MagicMock()
    tree.find_node_by_qualified_name = MagicMock(return_value=node)
    return tree


# ── Test 1: callers — 2-arg call + render via format_references ────────────


@pytest.mark.asyncio
async def test_lookup_callers_renders_via_ref_svc() -> None:
    """``show='callers'`` → ``ref_svc.callers(package, qname)`` 2-arg,
    output flows through ``format_references`` (H1 + lead + bullet)."""
    tree = _real_node_tree("pkg.helpers.compute")
    tree_svc = _tree_svc_for_module("pkg.helpers", tree)

    ref = NodeReference(
        from_package="pkg",
        from_node_id="pkg.cli.main",
        to_name="pkg.helpers.compute",
        to_node_id="pkg.helpers.compute",
        kind=ReferenceKind.CALLS,
    )
    ref_svc = MagicMock()
    ref_svc.callers = AsyncMock(return_value=(ref,))

    svc = LookupService(
        package_lookup=_pkg_lookup_mock(), tree_svc=tree_svc, ref_svc=ref_svc,
    )
    out = await svc.lookup(
        LookupInput(target="pkg.helpers.compute", show="callers")
    )

    # 2-arg call per Decision C1.
    ref_svc.callers.assert_awaited_once_with("pkg", "pkg.helpers.compute")
    # Output is real format_references markdown (NOT the old
    # placeholder "(no references)" or the bullet list from _render_refs).
    assert out.startswith("# Callers of `pkg.helpers.compute`\n"), out
    assert "1 references found (1 resolved, 0 unresolved)." in out, out
    assert "- `pkg.cli.main` → `pkg.helpers.compute`" in out, out


# ── Test 2: callees — 2-arg call ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_lookup_callees_renders_via_ref_svc() -> None:
    """``show='callees'`` → ``ref_svc.callees(package, qname)`` 2-arg."""
    tree = _real_node_tree("pkg.helpers.compute")
    tree_svc = _tree_svc_for_module("pkg.helpers", tree)

    ref = NodeReference(
        from_package="pkg",
        from_node_id="pkg.helpers.compute",
        to_name="pkg.helpers.normalize",
        to_node_id="pkg.helpers.normalize",
        kind=ReferenceKind.CALLS,
    )
    ref_svc = MagicMock()
    ref_svc.callees = AsyncMock(return_value=(ref,))

    svc = LookupService(
        package_lookup=_pkg_lookup_mock(), tree_svc=tree_svc, ref_svc=ref_svc,
    )
    out = await svc.lookup(
        LookupInput(target="pkg.helpers.compute", show="callees")
    )

    ref_svc.callees.assert_awaited_once_with("pkg", "pkg.helpers.compute")
    assert out.startswith("# Callees of `pkg.helpers.compute`\n"), out
    assert "- `pkg.helpers.compute` → `pkg.helpers.normalize`" in out, out


# ── Test 3: inherits — find_by_name(qname, kind=INHERITS) ──────────────────


@pytest.mark.asyncio
async def test_lookup_inherits_uses_find_by_name() -> None:
    """``show='inherits'`` for a class with ref_svc wired routes through
    ``ref_svc.find_by_name(qname, kind=ReferenceKind.INHERITS)`` — the
    INHERITS reference graph is the source of truth once #5c wires the
    service in. The class-only node-kind check still applies (only
    classes can inherit), but bases come from ref_svc, not from
    ``node.extra_metadata`` (the degraded-mode path remains for
    ref_svc=None — covered by existing test_lookup_service.py)."""
    tree = _real_node_tree("pkg.api.MyClass", kind="class")
    tree_svc = _tree_svc_for_module("pkg.api", tree)

    base_ref = NodeReference(
        from_package="pkg",
        from_node_id="pkg.api.MyClass",
        to_name="pkg.base.BaseModel",
        to_node_id="pkg.base.BaseModel",
        kind=ReferenceKind.INHERITS,
    )
    ref_svc = MagicMock()
    ref_svc.find_by_name = AsyncMock(return_value=(base_ref,))

    svc = LookupService(
        package_lookup=_pkg_lookup_mock(), tree_svc=tree_svc, ref_svc=ref_svc,
    )
    out = await svc.lookup(
        LookupInput(target="pkg.api.MyClass", show="inherits")
    )

    ref_svc.find_by_name.assert_awaited_once_with(
        "pkg.api.MyClass", kind=ReferenceKind.INHERITS,
    )
    assert out.startswith("# Bases of `pkg.api.MyClass`\n"), out
    assert "pkg.base.BaseModel" in out, out


# ── Test 4: NullReferenceService raises with YAML-anchored message ─────────


@pytest.mark.asyncio
async def test_lookup_callers_with_null_ref_svc_raises_with_yaml_message() -> None:
    """The post-I9 error message points users at the YAML knob
    (``reference_graph.capture.enabled``) — raised from inside
    ``NullReferenceService.callers`` rather than from a sentinel
    branch in ``LookupService._symbol_lookup``.  Same user-visible
    error contract — the forcing function (user hits error → edits
    YAML → re-runs) is preserved."""
    tree = _real_node_tree("pkg.helpers.compute")
    tree_svc = _tree_svc_for_module("pkg.helpers", tree)

    svc = LookupService(
        package_lookup=_pkg_lookup_mock(),
        tree_svc=tree_svc,
        ref_svc=NullReferenceService(),
    )
    with pytest.raises(ServiceUnavailableError) as excinfo:
        await svc.lookup(
            LookupInput(target="pkg.helpers.compute", show="callers")
        )
    msg = str(excinfo.value)
    assert "reference_graph.capture.enabled" in msg, msg
    assert "sub-PR #5b" not in msg, msg


# ── Test 5: empty rows render canonical empty markdown ─────────────────────


@pytest.mark.asyncio
async def test_lookup_callers_zero_rows_renders_empty_message() -> None:
    """No callers found → ``# Callers of `qname` \\nNo callers found.\\n``
    via ``format_references`` — NOT the pre-#5c ``(no references)``
    placeholder. The canonical empty shape keeps downstream parsers from
    branching on populated-vs-empty rendering."""
    tree = _real_node_tree("pkg.helpers.compute")
    tree_svc = _tree_svc_for_module("pkg.helpers", tree)

    ref_svc = MagicMock()
    ref_svc.callers = AsyncMock(return_value=())

    svc = LookupService(
        package_lookup=_pkg_lookup_mock(), tree_svc=tree_svc, ref_svc=ref_svc,
    )
    out = await svc.lookup(
        LookupInput(target="pkg.helpers.compute", show="callers")
    )

    assert out.startswith("# Callers of `pkg.helpers.compute`\n"), out
    assert "No callers found." in out, out
    assert "(no references)" not in out, out
