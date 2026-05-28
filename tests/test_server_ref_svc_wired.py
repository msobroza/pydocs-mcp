"""Integration test pinning that the MCP server wires ref_svc via the factory.

Regression: final code review of sub-PR #5c caught that ``server.py::run``
was manually constructing ``LookupService(ref_svc=None)`` instead of using
:func:`build_sqlite_lookup_service`. The unit tests all passed because they
mocked ``LookupService`` or tested the factory directly — none exercised
the actual ``server.py::run`` composition path. This test fills that gap:
it inspects the ``LookupService`` instance that ``server.py::run`` builds
and asserts ``ref_svc`` is a real ``ReferenceService``.

If this regresses, the MCP server's ``lookup(show="callers"|"callees"|
"inherits")`` tool will silently degrade to ``ServiceUnavailableError``
in production — defeating the entire user-visible payoff of #5c.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


class _FakeMCP:
    """Captures tool registrations from FastMCP without starting a server.

    Mirrors ``tests/test_server.py::FakeMCP`` so this test boots the exact
    same composition path as the rest of the server test suite. Accepts
    arbitrary kwargs on both construction (``instructions=``) and the
    ``tool`` decorator (``annotations=``).
    """

    def __init__(self, name: str, **kwargs: object) -> None:
        self.name = name
        self.kwargs = kwargs
        self.tools: dict[str, object] = {}

    def tool(self, **kwargs: object):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator

    def run(self, transport: str | None = None) -> None:
        pass


def test_server_run_wires_real_ref_svc(tmp_path: Path, monkeypatch) -> None:
    """AC: MCP server's ``LookupService.ref_svc`` is a real ``ReferenceService``.

    Approach: shadow ``LookupService.__init__`` to capture every instance
    constructed during ``server.run``, then boot ``run`` with ``FakeMCP``
    so it returns after wiring (no stdio loop). The captured ``ref_svc``
    must be a ``ReferenceService`` — not ``None`` — for the MCP lookup
    tool's callers/callees/inherits modes to function.
    """
    from pydocs_mcp.application.lookup_service import LookupService
    from pydocs_mcp.application.reference_service import ReferenceService
    from pydocs_mcp.db import open_index_database

    # Fresh DB so server.run doesn't fail on missing schema.
    db_path = tmp_path / "x.db"
    open_index_database(db_path).close()

    captured: dict[str, object] = {}
    orig_init = LookupService.__init__

    def _capture_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        captured["ref_svc"] = self.ref_svc

    monkeypatch.setattr(LookupService, "__init__", _capture_init)

    fake_mcp = _FakeMCP("test")
    fake_mcp_module = MagicMock()
    fake_mcp_module.FastMCP = lambda name, **kwargs: fake_mcp

    with patch.dict(
        sys.modules,
        {
            "mcp": MagicMock(),
            "mcp.server": MagicMock(),
            "mcp.server.fastmcp": fake_mcp_module,
            # ``server.run`` imports ``mcp.types.ToolAnnotations`` for the
            # readOnly / idempotent / openWorld advisory hints attached to
            # each MCP tool.
            "mcp.types": MagicMock(),
        },
    ):
        from pydocs_mcp.server import run

        run(db_path)

    assert "ref_svc" in captured, (
        "LookupService was never constructed during server.run — wiring missing"
    )
    assert captured["ref_svc"] is not None, (
        "server.py is constructing LookupService(ref_svc=None) — the MCP "
        "lookup tool's callers/callees/inherits modes will raise "
        "ServiceUnavailableError. Fix: use build_sqlite_lookup_service "
        "(see storage/factories.py) instead of manual construction."
    )
    assert isinstance(captured["ref_svc"], ReferenceService), (
        f"Expected ReferenceService instance, got {type(captured['ref_svc'])}"
    )
