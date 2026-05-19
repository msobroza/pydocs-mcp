"""File discoverers — one file per strategy.

Two concrete implementations of the
:class:`~pydocs_mcp.extraction.protocols.ProjectFileDiscoverer` /
:class:`~pydocs_mcp.extraction.protocols.DependencyFileDiscoverer`
Protocols:

- :mod:`.project` — :class:`ProjectFileDiscoverer` (walks a project dir)
- :mod:`.dependency` — :class:`DependencyFileDiscoverer` (lists files
  shipped by an installed dependency distribution)

Both consult the HARDCODED
:data:`~pydocs_mcp.extraction.config._EXCLUDED_DIRS` module constant for
directory pruning (spec decision #6b). The Protocol types share names
with the concrete classes; consumers that need the structural Protocol
import from :mod:`.base_discoverer`.
"""
from __future__ import annotations

from pydocs_mcp.extraction.strategies.discovery.dependency import (
    DependencyFileDiscoverer,
)
from pydocs_mcp.extraction.strategies.discovery.project import ProjectFileDiscoverer

__all__ = ("DependencyFileDiscoverer", "ProjectFileDiscoverer")
