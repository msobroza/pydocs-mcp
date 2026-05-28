"""Pin Task 8's composition wiring (sub-PR #5c §8.2).

These two assertions are the user-visible payoff of #5c:

1. ``build_sqlite_lookup_service`` returns a ``LookupService`` whose
   ``ref_svc`` is a real ``ReferenceService`` (was ``None`` through #5b).
2. ``ReferenceService`` is re-exported from ``pydocs_mcp.application`` —
   the public composition surface — so downstream wiring (CLI, server,
   tests) imports it through the package, not the internal module path.

If either regresses, the lookup tool's ``show="callers"|"callees"`` mode
silently degrades back to ``ServiceUnavailableError``.
"""

from __future__ import annotations


def test_build_sqlite_lookup_service_constructs_real_ref_svc(tmp_path):
    """``build_sqlite_lookup_service`` wires a real ``ReferenceService`` into
    ``LookupService.ref_svc`` (post-#5c — was ``None`` in #5b)."""
    from pydocs_mcp.application.reference_service import ReferenceService
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.storage.factories import build_sqlite_lookup_service

    db = tmp_path / "x.db"
    open_index_database(db).close()
    svc = build_sqlite_lookup_service(db)

    assert svc.ref_svc is not None
    assert isinstance(svc.ref_svc, ReferenceService)


def test_application_package_reexports_reference_service():
    """``ReferenceService`` is part of the public ``pydocs_mcp.application``
    surface — both as an attribute and in ``__all__``."""
    import pydocs_mcp.application as app
    from pydocs_mcp.application.reference_service import ReferenceService

    assert hasattr(app, "ReferenceService")
    assert app.ReferenceService is ReferenceService
    assert "ReferenceService" in app.__all__
