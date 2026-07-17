"""Pins the ``_run_tool`` error boundary contract (server.py §5.2 docstring).

``_run_tool`` is the shared try/except every one of the six MCP handlers
delegates through: typed :class:`MCPToolError` subclasses must re-raise
UNCHANGED (first except arm), anything else must be logged then wrapped in
:class:`ServiceUnavailableError` (second arm). Only the ``get_symbol`` ->
``NotFoundError`` pass-through was pinned via real service wiring
(``TestSymbolWithTreeService`` in ``tests/test_server.py``); the generic
contract — including the wrap arm and the except-clause ORDER — had zero
direct coverage, so a refactor that reordered the clauses (wrapping typed
errors too) or reverted to a blanket ``except Exception: return str(e)``
would pass the whole suite undetected.
"""

from __future__ import annotations

import asyncio

import pytest

from pydocs_mcp.application import (
    InvalidArgumentError,
    NotFoundError,
    ServiceUnavailableError,
)
from pydocs_mcp.application.tool_response import ENVELOPE_MODELS


def _arun(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestRunToolErrorBoundary:
    """Direct unit coverage of ``server._run_tool`` — no MCP/DB wiring needed
    since it's a plain module-level function taking a zero-arg async ``produce``."""

    def test_unexpected_exception_is_wrapped_in_service_unavailable(self) -> None:
        """A non-MCPToolError raised mid-flight (e.g. sqlite3.OperationalError,
        or any bare KeyError/RuntimeError bug in a tool body) must surface as
        ServiceUnavailableError('<tool> failed: <original message>'), never as
        the raw exception type and never swallowed into a string return."""
        from pydocs_mcp.server import _run_tool

        async def _boom():
            raise RuntimeError("boom")

        with pytest.raises(ServiceUnavailableError, match=r"search_codebase failed: boom"):
            _arun(_run_tool("search_codebase", _boom, ENVELOPE_MODELS["search_codebase"]))

    def test_unexpected_exception_chains_the_original_as_cause(self) -> None:
        """``raise ... from e`` must be preserved so tracebacks/logs keep the
        original exception reachable via ``__cause__`` — losing this would
        make `log.exception` + wrapped errors much harder to debug."""
        from pydocs_mcp.server import _run_tool

        original = KeyError("missing_field")

        async def _boom():
            raise original

        with pytest.raises(ServiceUnavailableError) as exc_info:
            _arun(_run_tool("get_context", _boom, ENVELOPE_MODELS["get_context"]))

        assert exc_info.value.__cause__ is original

    @pytest.mark.parametrize("typed_error", [InvalidArgumentError, NotFoundError])
    def test_typed_mcp_tool_error_passes_through_unwrapped(self, typed_error) -> None:
        """Typed MCPToolError subclasses must re-raise UNCHANGED — the first
        except arm, checked BEFORE the generic wrap arm. If the clause order
        were ever reversed (or collapsed into one blanket handler), this would
        catch the typed error and re-wrap it as ServiceUnavailableError,
        destroying its structured JSON-RPC shape/type for the MCP client."""
        from pydocs_mcp.server import _run_tool

        async def _raise_typed():
            raise typed_error("domain-specific message")

        with pytest.raises(typed_error, match="domain-specific message"):
            _arun(_run_tool("get_symbol", _raise_typed, ENVELOPE_MODELS["get_symbol"]))

    def test_service_unavailable_itself_is_not_double_wrapped(self) -> None:
        """ServiceUnavailableError IS an MCPToolError — re-raising it through
        the first except arm must not produce a nested
        'get_why failed: get_why failed: ...' message."""
        from pydocs_mcp.server import _run_tool

        async def _raise_service_unavailable():
            raise ServiceUnavailableError("upstream pipeline unavailable")

        with pytest.raises(ServiceUnavailableError, match=r"^upstream pipeline unavailable$"):
            _arun(_run_tool("get_why", _raise_service_unavailable, ENVELOPE_MODELS["get_why"]))
