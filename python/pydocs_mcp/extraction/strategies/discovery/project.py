"""ProjectFileDiscoverer — walks a project directory.

Prunes directories listed in :data:`_EXCLUDED_DIRS` in-place during
``os.walk`` so virtualenvs and build artefacts are never descended into.
Filters files by ``scope.include_extensions`` and skips anything larger
than ``scope.max_file_size_bytes``. Output paths are sorted for
deterministic downstream hashing.

The exclusion list is the HARDCODED
:data:`~pydocs_mcp.extraction.config._EXCLUDED_DIRS` — never
``self.scope`` — because un-excluded ``.git`` / ``.venv`` /
``site-packages`` would leak secrets, balloon the FTS index, and break
inspect-mode imports (spec decision #6b). Users narrow the extension
allowlist via YAML; users cannot widen or shrink the directory
blocklist.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies.discovery._shared import _within_size_budget


@dataclass(frozen=True, slots=True)
class ProjectFileDiscoverer:
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
                full = str(Path(dirpath) / name)
                if not _within_size_budget(full, self.scope.max_file_size_bytes):
                    continue
                paths.append(full)
        paths.sort()
        return paths, Path(target)


__all__ = ("ProjectFileDiscoverer",)
