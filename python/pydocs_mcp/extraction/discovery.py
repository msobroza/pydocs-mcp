"""Project + Dependency file discoverers (spec §5, §11.1, decision #6b).

Two concrete implementations of the sub-PR #5 ``ProjectFileDiscoverer`` /
``DependencyFileDiscoverer`` Protocols (``extraction/protocols.py``).

Both consult the HARDCODED :data:`~pydocs_mcp.extraction.config._EXCLUDED_DIRS`
module constant for directory pruning — never ``self.scope`` — because
un-excluded ``.git`` / ``.venv`` / ``site-packages`` would leak secrets, balloon
the FTS index, and break inspect-mode imports (spec decision #6b). Users narrow
the extension allowlist via YAML; users cannot widen or shrink the directory
blocklist. ``DiscoveryScopeConfig.extra="forbid"`` catches attempts to add
``exclude_dirs`` at load time.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction._dep_helpers import (
    find_installed_distribution,
    find_site_packages_root,
)
from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, DiscoveryScopeConfig


@dataclass(frozen=True, slots=True)
class ProjectFileDiscoverer:
    """Walks a project directory, returns ``(paths, root=project_dir)``.

    Prunes directories listed in :data:`_EXCLUDED_DIRS` in-place during
    ``os.walk`` so virtualenvs and build artefacts are never descended into.
    Filters files by ``scope.include_extensions`` and skips anything larger
    than ``scope.max_file_size_bytes``. Output paths are sorted for
    deterministic downstream hashing.
    """

    scope: DiscoveryScopeConfig

    def discover(self, target: Path) -> tuple[list[str], Path]:
        paths: list[str] = []
        for dirpath, dirnames, filenames in os.walk(target):
            # Prune in-place so os.walk skips excluded subtrees entirely.
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
            for name in filenames:
                ext = Path(name).suffix.lower()
                if ext not in self.scope.include_extensions:
                    continue
                full = os.path.join(dirpath, name)
                if not _within_size_budget(full, self.scope.max_file_size_bytes):
                    continue
                paths.append(full)
        paths.sort()
        return paths, Path(target)


@dataclass(frozen=True, slots=True)
class DependencyFileDiscoverer:
    """Lists files shipped by an installed dependency distribution.

    Returns ``(paths, site_packages_root)``; a missing distribution
    (declared-but-not-installed) returns ``([], Path("."))`` — the
    :class:`~pydocs_mcp.application.IndexProjectService` treats that as a
    non-fatal skip. Applies the same extension + size + directory-blocklist
    filters as :class:`ProjectFileDiscoverer`, because a wheel can ship
    bundled ``.git/`` / ``__pycache__`` / ``node_modules`` directories and
    they must never leak into the FTS index.
    """

    scope: DiscoveryScopeConfig

    def discover(self, target: str) -> tuple[list[str], Path]:
        dist = find_installed_distribution(target)
        if dist is None:
            return [], Path(".")
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
        root = Path(find_site_packages_root(paths[0])) if paths else Path(".")
        return paths, root


def _within_size_budget(path: str, max_bytes: int) -> bool:
    """Return ``True`` iff the file exists and its size ≤ ``max_bytes``.

    Missing / unreadable files are dropped silently — ``os.getsize`` raising
    means the downstream reader would also fail, so there's no point
    surfacing an error here.
    """
    try:
        return os.path.getsize(path) <= max_bytes
    except OSError:
        return False


def _in_excluded_dir(relpath: str) -> bool:
    """True iff any path component of ``relpath`` is a blocklisted directory.

    Guards against dependency wheels that ship vestigial ``.git`` or
    ``__pycache__`` directories (rare but real — spec §11.1 rationale).
    """
    return any(part in _EXCLUDED_DIRS for part in Path(relpath).parts)


__all__ = (
    "DependencyFileDiscoverer",
    "ProjectFileDiscoverer",
)
