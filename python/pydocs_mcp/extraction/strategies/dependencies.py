"""Static dependency resolver — wraps ``deps.discover_declared_dependencies`` (spec §10).

:class:`StaticDependencyResolver` is the only :class:`DependencyResolver`
strategy shipped in sub-PR #5. Today's :mod:`pydocs_mcp.deps` is already
clean — pure functions, no I/O beyond file reads — so we wrap rather than
rewrite it. Alternative strategies (poetry.lock / pdm.lock / uv resolution /
graph-aware dependency walking) are future work.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StaticDependencyResolver:
    """Implements sub-PR #4's :class:`DependencyResolver` Protocol via
    :func:`pydocs_mcp.deps.discover_declared_dependencies`.

    Scans ``pyproject.toml`` + ``requirements*.txt`` anywhere under
    ``project_dir``; returns sorted, normalized, de-duplicated names as a
    tuple (the Protocol's return type).
    """

    async def resolve(self, project_dir: Path) -> tuple[str, ...]:
        return await asyncio.to_thread(self._resolve_sync, project_dir)

    def _resolve_sync(self, project_dir: Path) -> tuple[str, ...]:
        # Deferred import keeps the module load graph small for test-time
        # imports; :mod:`deps` pulls in ``tomllib`` which is fine but
        # unnecessary at class-definition time.
        from pydocs_mcp.deps import discover_declared_dependencies

        return tuple(discover_declared_dependencies(str(project_dir)))


__all__ = ("StaticDependencyResolver",)
