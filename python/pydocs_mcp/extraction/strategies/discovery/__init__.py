"""File discoverers — one file per strategy.

Two concrete implementations of the
:class:`~pydocs_mcp.extraction.protocols.ProjectFileDiscoverer` /
:class:`~pydocs_mcp.extraction.protocols.DependencyFileDiscoverer`
Protocols:

- :mod:`.project` — :class:`ProjectFileDiscoverer` (walks a project dir)
- :mod:`.dependency` — :class:`DependencyFileDiscoverer` (lists files
  shipped by an installed dependency distribution)

Both prune against the EFFECTIVE exclusion set: the hardcoded
:data:`~pydocs_mcp.extraction.config._EXCLUDED_DIRS` floor
(non-removable) unioned with the additive user surfaces — YAML
``extraction.discovery.*.exclude_dirs`` on both scopes; the project
walk additionally honors the indexed project's own
``[tool.pydocs-mcp] exclude_dirs`` (spec decision #6b as amended
2026-07-13: additive-only, the floor never shrinks). The Protocol
types share names with the concrete classes; consumers that need the
structural Protocol import from :mod:`.base_discoverer`.
"""

from __future__ import annotations

from pydocs_mcp.extraction.strategies.discovery.dependency import (
    DependencyFileDiscoverer,
)
from pydocs_mcp.extraction.strategies.discovery.project import ProjectFileDiscoverer

__all__ = ("DependencyFileDiscoverer", "ProjectFileDiscoverer")
