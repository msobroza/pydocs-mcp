"""PackageBuildStage — fills ``state.package``; branches on ``state.files.target_kind``.

PROJECT path produces the canonical ``Package(name="__project__", ...)``
consumed by :class:`ProjectIndexer`. DEPENDENCY path walks
``importlib.metadata.Distribution`` metadata — a missing distribution
raises :class:`LookupError` so the service layer can translate into a
non-fatal skip one level up (declared-but-not-installed deps are common
during local development; the stage keeps its contract honest by
raising).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState, TargetKind
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Package, PackageOrigin


@stage_registry.register("package_build")
@dataclass(frozen=True, slots=True)
class PackageBuildStage:
    name: str = "package_build"

    async def run(self, state: IngestionState) -> IngestionState:
        pkg = await asyncio.to_thread(self._build, state)
        return replace(state, package=pkg)

    def _build(self, state: IngestionState) -> Package:
        if state.files.target_kind is TargetKind.PROJECT:
            return self._project_package(state)
        return self._dep_package(state)

    def _project_package(self, state: IngestionState) -> Package:
        target = Path(str(state.files.target))
        return Package(
            name=PROJECT_PACKAGE_NAME,
            version="local",
            summary=f"Project: {target.name}",
            homepage="",
            dependencies=(),
            content_hash=state.files.content_hash,
            origin=PackageOrigin.PROJECT,
        )

    def _dep_package(self, state: IngestionState) -> Package:
        # Deferred imports keep the heavy importlib.metadata machinery out of
        # module-load-time for callers that never hit the dep branch.
        from pydocs_mcp.deps import normalize_package_name
        from pydocs_mcp.extraction.strategies._dep_helpers import (
            find_installed_distribution,
        )
        dep_name = str(state.files.target)
        dist = find_installed_distribution(dep_name)
        if dist is None:
            raise LookupError(f"dependency {dep_name!r} is not installed")
        raw_name = dist.metadata["Name"] or dep_name
        name = normalize_package_name(raw_name)
        version = dist.metadata["Version"] or "?"
        summary = dist.metadata["Summary"] or ""
        homepage = dist.metadata["Home-page"] or ""
        deps = tuple(
            r.split(";")[0].strip() for r in (dist.requires or [])[:50]
        )
        return Package(
            name=name,
            version=version,
            summary=summary,
            homepage=homepage,
            dependencies=deps,
            content_hash=state.files.content_hash,
            origin=PackageOrigin.DEPENDENCY,
        )

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> PackageBuildStage:
        return cls()

    def to_dict(self) -> dict:
        return {"type": "package_build"}


__all__ = ("PackageBuildStage",)
