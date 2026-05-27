"""Public exception hierarchy.

All exceptions raised by pydocs-mcp inherit from :class:`PydocsMCPError`,
so callers can ``except PydocsMCPError`` to catch any library-originated
failure without swallowing unrelated bugs.

Concrete exception classes (``MCPToolError``, ``UnitOfWorkNotEnteredError``,
``PipelineLoadError``, ...) live in their respective modules close to the
code that raises them; only the root + the rule lives here.
"""
from __future__ import annotations


class PydocsMCPError(Exception):
    """Base class for all exceptions raised by pydocs-mcp.

    Subclasses MAY also inherit from the relevant standard-library
    builtin (``ValueError``, ``RuntimeError``, ...) via multiple
    inheritance — see ``UnitOfWorkNotEnteredError`` and
    ``PipelineLoadError`` for examples. The PydocsMCPError lineage is
    the catch-any-pydocs-mcp-failure handle; the builtin lineage
    preserves existing isinstance checks at older call sites.
    """
