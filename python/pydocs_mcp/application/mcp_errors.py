"""Typed exception hierarchy for MCP tool handlers (sub-PR #6 §5.1).

Handlers raise these instead of returning error strings. FastMCP maps
them to JSON-RPC error responses; see spec §5.3 for the code mapping.
"""
from __future__ import annotations


class MCPToolError(Exception):
    """Base — every handler-raised error inherits from this."""


class InvalidArgumentError(MCPToolError):
    """Semantic validation failure — input parsed but domain-invalid.

    Pydantic ``ValidationError`` covers schema-level failures; this is
    for post-parse checks (e.g., ``show="inherits"`` on a non-class target).
    """


class NotFoundError(MCPToolError):
    """Specified target doesn't exist in the index.

    Raised by ``lookup`` for unknown packages/modules/symbols.
    NOT raised by ``search`` — an empty search returns success with
    an empty-result string.
    """


class ServiceUnavailableError(MCPToolError):
    """Backend raised an unexpected error (SQLite, pipeline) or a
    required optional service is missing (e.g. tree_svc is None but
    ``show="tree"`` was requested).
    """
