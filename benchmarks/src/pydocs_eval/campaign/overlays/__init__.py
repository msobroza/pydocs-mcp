"""Shipped serve-YAML overlays for campaign cells (ADR 0021 eval hook).

Each ``*.yaml`` here is a named serve overlay a campaign cell can select
(``CellConfig.suggestion_overlay`` today, a future multilang on/off dimension
next). ``campaign/overlays.py`` maps the cell's overlay NAME to the file in this
package and threads it through ``pydocs-mcp --config`` into the rollout
``.mcp.json``. Packaged as ``package-data`` so a built install resolves them too.
"""
