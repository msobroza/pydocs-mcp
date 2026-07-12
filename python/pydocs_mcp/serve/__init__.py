"""Serve-side helpers — file watcher today; HTTP transport tomorrow.

Kept separate from `pydocs_mcp.server` (the FastMCP entry point) as a
leaf package for import-cost hygiene: default `pydocs-mcp serve`
(no `--watch`) never imports anything in this package, so it pays no
watcher import cost.
"""
