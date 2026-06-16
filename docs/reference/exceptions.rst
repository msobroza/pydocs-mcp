Exceptions
==========

The public exception hierarchy. :class:`~pydocs_mcp.exceptions.PydocsMCPError` is
the root (re-exported from the package root); the MCP-facing errors below are
raised by the ``search`` and ``lookup`` tool handlers.

.. autoexception:: pydocs_mcp.exceptions.PydocsMCPError

.. automodule:: pydocs_mcp.application.mcp_errors
   :members: MCPToolError, InvalidArgumentError, NotFoundError, ServiceUnavailableError
