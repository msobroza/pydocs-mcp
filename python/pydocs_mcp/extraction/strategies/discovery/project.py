"""ProjectFileDiscoverer — walks a project directory.

Prunes directories in-place during ``os.walk`` so excluded subtrees are
never descended into. Filters files by ``scope.include_extensions`` and
skips anything larger than ``scope.max_file_size_bytes``. Output paths
are sorted for deterministic downstream hashing; the effective exclusion
set the walk pruned against is returned as the third element so
downstream stages fold/consume the exact set used (spec D10).

The pruning set is the EFFECTIVE exclusion set (spec decision #6b, as
amended 2026-07-13): the hardcoded
:data:`~pydocs_mcp.extraction.config._EXCLUDED_DIRS` FLOOR — never
removable, because un-excluded ``.git`` / ``.venv`` / ``site-packages``
would leak secrets, balloon the FTS index, and break inspect-mode
imports — unioned with the two ADDITIVE user surfaces: YAML
``extraction.discovery.project.exclude_dirs`` and the indexed project's
own ``[tool.pydocs-mcp] exclude_dirs``. Users can exclude MORE than the
floor, never less.

The pyproject surface is read PER RUN through the injected
``excludes_loader`` — not captured at composition time — so a
``--watch``-triggered reindex applies fresh ``pyproject.toml`` exclude
edits without a server restart (spec D3).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies.discovery._shared import _within_size_budget
from pydocs_mcp.project_toml import (
    ProjectExcludes,
    load_project_excludes,
    merge_excludes,
)


def _dir_survives(
    root: Path,
    dirpath: str,
    name: str,
    effective: ProjectExcludes,
) -> bool:
    """True iff the candidate directory is NOT excluded.

    Bare names are an O(1) frozenset test on the leaf name. Anchored
    entries need the walk-root-relative POSIX path of the candidate
    directory — computed only when anchored entries exist, keeping the
    no-excludes path byte-identical to floor-only pruning. Pruning at
    the directory level means excluded subtrees are never descended,
    so per-file checks never happen (spec §7.3).
    """
    if name in effective.names:
        return False
    if not effective.anchored:
        return True
    rel = (Path(dirpath) / name).relative_to(root).as_posix()
    return not effective.matches(rel)


@dataclass(frozen=True, slots=True)
class ProjectFileDiscoverer:
    scope: DiscoveryScopeConfig
    # Injected strategy, not a startup capture: every discover() run
    # re-reads the project's pyproject.toml so --watch reindexes pick up
    # [tool.pydocs-mcp] exclude_dirs edits without a restart (spec D3).
    excludes_loader: Callable[[Path], ProjectExcludes] = load_project_excludes

    def discover(self, target: Path) -> tuple[list[str], Path, ProjectExcludes]:
        root = Path(target)
        effective = merge_excludes(
            _EXCLUDED_DIRS,
            self.scope.exclude_dirs,
            self.excludes_loader(root),
        )
        paths: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune in-place so os.walk skips excluded subtrees entirely.
            dirnames[:] = [d for d in dirnames if _dir_survives(root, dirpath, d, effective)]
            for name in filenames:
                ext = Path(name).suffix.lower()
                if ext not in self.scope.include_extensions:
                    continue
                full = str(Path(dirpath) / name)
                if not _within_size_budget(full, self.scope.max_file_size_bytes):
                    continue
                paths.append(full)
        paths.sort()
        return paths, root, effective


__all__ = ("ProjectFileDiscoverer",)
