"""Shared base / contracts for file discoverers.

Re-exports both Protocols from :mod:`pydocs_mcp.extraction.protocols` so
each concrete-discoverer file imports its contract from one obvious
place. Project and dependency discovery have different inputs (path
vs. distribution name) so they're separate Protocols rather than one
parameterized abstraction.
"""
from __future__ import annotations

from pydocs_mcp.extraction.protocols import (
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)

__all__ = ("DependencyFileDiscoverer", "ProjectFileDiscoverer")
