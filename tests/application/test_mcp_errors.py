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
