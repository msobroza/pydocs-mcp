"""Serve-side helpers — file watcher today; HTTP transport tomorrow.

Kept separate from `pydocs_mcp.server` (the FastMCP entry point) so the
`watchdog` lazy import lives in a leaf module that's only loaded when
`--watch` is set. Default `pydocs-mcp serve` never imports anything in
this package.
"""
