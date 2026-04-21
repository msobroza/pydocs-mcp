"""Tests for the typed MCP exception hierarchy (sub-PR #6 §5.1)."""
from __future__ import annotations

import pytest

from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    MCPToolError,
    NotFoundError,
    ServiceUnavailableError,
)


def test_all_subclasses_inherit_from_mcptoolerror() -> None:
    assert issubclass(InvalidArgumentError, MCPToolError)
    assert issubclass(NotFoundError, MCPToolError)
    assert issubclass(ServiceUnavailableError, MCPToolError)


def test_mcptoolerror_is_an_exception() -> None:
    assert issubclass(MCPToolError, Exception)


def test_error_carries_message() -> None:
    err = NotFoundError("target 'foo' not found")
    assert str(err) == "target 'foo' not found"


def test_exceptions_raise_and_catch_as_base() -> None:
    with pytest.raises(MCPToolError):
        raise InvalidArgumentError("bad input")
    with pytest.raises(MCPToolError):
        raise NotFoundError("missing")
    with pytest.raises(MCPToolError):
        raise ServiceUnavailableError("backend down")


def test_new_application_exports_are_importable() -> None:
    """Sub-PR #6 §10 + AC #28 — application package re-exports everything
    ``server.py`` and the CLI need from one import path."""
    from pydocs_mcp.application import (
        InvalidArgumentError,
        LookupInput,
        LookupService,
        MCPToolError,
        NotFoundError,
        SearchInput,
        ServiceUnavailableError,
    )
    assert all([
        InvalidArgumentError, LookupInput, LookupService, MCPToolError,
        NotFoundError, SearchInput, ServiceUnavailableError,
    ])
