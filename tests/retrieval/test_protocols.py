"""Protocol smoke tests — cross-cutting structural types."""

from __future__ import annotations

from pydocs_mcp.retrieval.protocols import (
    ConnectionProvider,
    ResultFormatter,
)


def test_protocol_imports():
    assert hasattr(ConnectionProvider, "__mro__")
    assert hasattr(ResultFormatter, "__mro__")
