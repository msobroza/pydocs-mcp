"""Pin #5b's deferred-wire state — #5c will flip these (spec §8.2)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
from pydocs_mcp.application.mcp_inputs import LookupInput


@pytest.mark.asyncio
async def test_lookup_show_callers_still_raises_service_unavailable_in_5b() -> None:
    """#5b ships ReferenceService but does NOT wire it. The error message
    stays as 'enable via sub-PR #5b' until #5c flips the wire."""
    fake_node = MagicMock()
    fake_node.node_id = "x"
    fake_node.kind = "method"
    fake_tree = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = MagicMock()

    # ``exists`` must only match the module (``pkg.mod``), NOT ``pkg.mod.x``,
    # else _longest_indexed_module greedily consumes the symbol segment and
    # routes to _module_lookup instead of _symbol_lookup.
    async def _fake_exists(pkg, mod):
        return mod == "pkg.mod"
    async def _fake_get_tree(pkg, mod):
        return fake_tree
    tree_svc.exists = _fake_exists
    tree_svc.get_tree = _fake_get_tree

    pkg_lookup = MagicMock()
    async def _list_packages():
        return ()
    pkg_lookup.list_packages = _list_packages
    async def _find_module(pkg, mod):
        return False
    pkg_lookup.find_module = _find_module

    svc = LookupService(
        package_lookup=pkg_lookup, tree_svc=tree_svc, ref_svc=None,
    )
    with pytest.raises(ServiceUnavailableError) as excinfo:
        await svc.lookup(LookupInput(target="pkg.mod.x", show="callers"))
    assert "sub-PR #5b" in str(excinfo.value)


@pytest.mark.asyncio
async def test_build_sqlite_lookup_service_still_passes_ref_svc_none(tmp_path):
    """#5b composition root: ref_svc=None. #5c flips this."""
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.storage.factories import build_sqlite_lookup_service

    db = tmp_path / "x.db"
    open_index_database(db).close()
    svc = build_sqlite_lookup_service(db)
    assert svc.ref_svc is None
