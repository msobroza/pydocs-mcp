"""Typed exceptions for the storage layer."""

from __future__ import annotations

from pydocs_mcp.exceptions import PydocsMCPError


class UnitOfWorkNotEnteredError(PydocsMCPError, RuntimeError):
    """Raised when a UoW attribute is accessed outside ``async with``.

    Repository attributes on :class:`UnitOfWork` are only valid inside
    the active transaction context. Loud failure beats silent None.
    """

    def __init__(self, attr_name: str) -> None:
        super().__init__(
            f"UnitOfWork attribute {attr_name!r} accessed outside "
            f"`async with uow:` — repositories are valid only inside "
            f"the transaction context.",
        )
        self.attr_name = attr_name
