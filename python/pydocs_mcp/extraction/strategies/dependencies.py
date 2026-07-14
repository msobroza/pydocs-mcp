"""Static dependency resolver — wraps ``deps.discover_declared_dependencies`` (spec §10).

:class:`StaticDependencyResolver` is the only :class:`DependencyResolver`
strategy shipped. Today's :mod:`pydocs_mcp.deps` is already clean — pure
functions, no I/O beyond file reads — so we wrap rather than rewrite it.
Alternative strategies (poetry.lock / pdm.lock / uv resolution / graph-aware
dependency walking) are future work.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS
from pydocs_mcp.project_toml import (
    ProjectExcludes,
    load_project_excludes,
    merge_excludes,
)


@dataclass(frozen=True, slots=True)
class StaticDependencyResolver:
    """Implements the :class:`DependencyResolver` Protocol via
    :func:`pydocs_mcp.deps.discover_declared_dependencies`.

    Scans ``pyproject.toml`` + ``requirements*.txt`` anywhere under
    ``project_dir``; returns sorted, normalized, de-duplicated names as a
    tuple (the Protocol's return type).

    ``excludes_loader`` + ``scope_exclude_dirs`` mirror the project
    discoverer: the effective project set (floor ∪ YAML project entries ∪
    pyproject TOML) is computed fresh per ``resolve`` call — the per-run read
    posture that keeps ``--watch`` edits live — and passed down so a manifest
    inside an excluded directory contributes no packages (spec D9). A fresh
    per-call TOML read is safe here: nothing fingerprints this set, so there
    is no intra-run coupling to preserve.
    """

    excludes_loader: Callable[[Path], ProjectExcludes] = load_project_excludes
    scope_exclude_dirs: tuple[str, ...] = ()

    async def resolve(self, project_dir: Path) -> tuple[str, ...]:
        return await asyncio.to_thread(self._resolve_sync, project_dir)

    def _resolve_sync(self, project_dir: Path) -> tuple[str, ...]:
        # Deferred import keeps the module load graph small for test-time
        # imports; :mod:`deps` pulls in ``tomllib`` which is fine but
        # unnecessary at class-definition time.
        from pydocs_mcp.deps import discover_declared_dependencies

        effective = merge_excludes(
            _EXCLUDED_DIRS,
            self.scope_exclude_dirs,
            self.excludes_loader(project_dir),
        )
        return tuple(discover_declared_dependencies(str(project_dir), effective))


__all__ = ("StaticDependencyResolver",)
