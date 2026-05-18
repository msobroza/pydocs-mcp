"""AC #17 — staged shipping: #5b builds ReferenceService but does NOT
re-export it from `pydocs_mcp.application`. #5c will add the re-export
AND delete this test file in the same commit."""
from __future__ import annotations


def test_reference_service_not_in_application_package_until_5c():
    """If this test breaks because someone re-exported ReferenceService
    from pydocs_mcp.application, you are in #5c territory — delete this
    file AND add the re-export to application/__init__.py."""
    import pydocs_mcp.application as app

    assert not hasattr(app, "ReferenceService")
    assert "ReferenceService" not in (app.__all__ or [])
