"""DependencyFileDiscoverer — lists files shipped by an installed dependency.

Returns ``(paths, site_packages_root)``; a missing distribution
(declared-but-not-installed) returns ``([], Path("."))`` — the
:class:`~pydocs_mcp.application.ProjectIndexer` treats that as a
non-fatal skip. Applies the same extension + size + directory-blocklist
filters as :class:`ProjectFileDiscoverer`, because a wheel can ship
bundled ``.git/`` / ``__pycache__`` / ``node_modules`` directories and
they must never leak into the FTS index.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies._dep_helpers import (
    find_installed_distribution,
    find_site_packages_root,
)
from pydocs_mcp.extraction.strategies.discovery._shared import (
    _in_excluded_dir,
    _within_size_budget,
)


@dataclass(frozen=True, slots=True)
class DependencyFileDiscoverer:
    scope: DiscoveryScopeConfig

    def discover(self, target: str) -> tuple[list[str], Path]:
        dist = find_installed_distribution(target)
        if dist is None:
            return [], Path()
        paths: list[str] = []
        for f in dist.files or []:
            rel_str = str(f)
            if _in_excluded_dir(rel_str):
                continue
            ext = Path(rel_str).suffix.lower()
            if ext not in self.scope.include_extensions:
                continue
            full = str(dist.locate_file(f))
            if not _within_size_budget(full, self.scope.max_file_size_bytes):
                continue
            paths.append(full)
        paths.sort()
        root = Path(find_site_packages_root(paths[0])) if paths else Path()
        return paths, root


__all__ = ("DependencyFileDiscoverer",)
