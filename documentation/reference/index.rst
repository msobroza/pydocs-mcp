API Reference
=============

``pydocs-mcp`` is primarily a **server / CLI tool**, so its supported public
surface is intentionally small: the exception hierarchy, the MCP server entry
point and its tool input schemas, and the application-layer services used by the
CLI / MCP composition root. Everything under ``extraction/``, ``retrieval/``, and
``storage/`` is internal and may change without notice.

.. toctree::
   :maxdepth: 2

   exceptions
   server
   application
