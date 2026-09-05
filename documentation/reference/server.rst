MCP Server
==========

The MCP surface is pinned at nine task-shaped tools — ``get_overview``,
``search_codebase``, ``get_symbol``, ``get_context``, ``get_references``,
``get_why``, ``grep``, ``glob`` and ``read_file`` — registered by
:func:`~pydocs_mcp.server.run` (frozen contract: ``docs/tool-contracts.md``).
The tools themselves are closures inside the server module; each validates the
matching pydantic input model below and delegates to the ``ToolRouter`` (the
three filesystem tools delegate to the ``FileToolsService``, which walks the
indexer's discovery scope). ``run`` serves a single project (``db_path``) or
several — a ``workspace`` directory or explicit ``db_paths`` for read-only
multi-repo serving — and accepts ``gpu=True`` to run query-time embedding on
CUDA.

.. autofunction:: pydocs_mcp.server.run

Tool input schemas
------------------

.. autoclass:: pydocs_mcp.application.mcp_inputs.OverviewInput
   :members:

.. autoclass:: pydocs_mcp.application.mcp_inputs.SearchInput
   :members:

.. autoclass:: pydocs_mcp.application.mcp_inputs.SymbolInput
   :members:

.. autoclass:: pydocs_mcp.application.mcp_inputs.ContextInput
   :members:

.. autoclass:: pydocs_mcp.application.mcp_inputs.ReferencesInput
   :members:

.. autoclass:: pydocs_mcp.application.mcp_inputs.WhyInput
   :members:

.. autoclass:: pydocs_mcp.application.mcp_inputs.GrepInput
   :members:

.. autoclass:: pydocs_mcp.application.mcp_inputs.GlobInput
   :members:

.. autoclass:: pydocs_mcp.application.mcp_inputs.ReadFileInput
   :members:

``LookupInput`` is no longer a tool schema: it survives as the internal
request contract that ``get_symbol`` / ``get_references`` build when
delegating to the lookup router (see ``application/tool_router.py``).
