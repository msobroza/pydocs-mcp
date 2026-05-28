"""Pin re-exports from pydocs_mcp.storage (sub-PR #5a follow-up)."""

from __future__ import annotations


def test_unit_of_work_not_entered_error_reexported_from_storage_package() -> None:
    """The errors module is one level deeper than the rest of the storage
    surface. Re-exporting from `pydocs_mcp.storage` keeps callers from
    having to remember the `errors` submodule path."""
    from pydocs_mcp.storage import UnitOfWorkNotEnteredError as exported
    from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError as direct

    assert exported is direct
