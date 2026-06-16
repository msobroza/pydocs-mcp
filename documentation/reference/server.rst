MCP Server
==========

The MCP surface is pinned at two tools — ``search`` and ``lookup`` — registered
by :func:`~pydocs_mcp.server.run`. The tools themselves are closures inside
``run``; their request contracts are the ``SearchInput`` and ``LookupInput``
models below.

.. autofunction:: pydocs_mcp.server.run

Tool input schemas
------------------

.. autoclass:: pydocs_mcp.application.mcp_inputs.SearchInput
   :members:

.. autoclass:: pydocs_mcp.application.mcp_inputs.LookupInput
   :members:
