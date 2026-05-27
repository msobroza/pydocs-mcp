"""FileDiscoveryStage — fills ``state.files.paths`` + ``state.files.root``.

Target-kind branch lives here. Holding BOTH discoverers (project and
dependency) and picking at runtime on ``state.files.target_kind`` keeps
the pipeline one-dimensional — the alternative (two pipelines, one per
kind) would duplicate the shared middle four stages and force callers
to pick (spec decision #19).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState, TargetKind
from pydocs_mcp.extraction.serialization import stage_registry

if TYPE_CHECKING:
    from pydocs_mcp.extraction.strategies.discovery import (
        DependencyFileDiscoverer,
        ProjectFileDiscoverer,
    )


@stage_registry.register("file_discovery")
@dataclass(frozen=True, slots=True)
class FileDiscoveryStage:
    project_discoverer: "ProjectFileDiscoverer"
    dep_discoverer: "DependencyFileDiscoverer"
    name: str = "file_discovery"

    async def run(self, state: IngestionState) -> IngestionState:
        paths, root = await asyncio.to_thread(self._discover, state)
        new_files = replace(state.files, paths=tuple(paths), root=root)
        return replace(state, files=new_files)

    def _discover(self, state: IngestionState) -> tuple[list[str], Path]:
        if state.files.target_kind is TargetKind.PROJECT:
            return self.project_discoverer.discover(Path(str(state.files.target)))
        return self.dep_discoverer.discover(str(state.files.target))

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "FileDiscoveryStage":
        # Deferred import avoids importing concrete discoverers at registry
        # construction time — keeps the registry decode path free of
        # side-effect-heavy filesystem-aware modules.
        from pydocs_mcp.extraction.strategies.discovery import (
            DependencyFileDiscoverer,
            ProjectFileDiscoverer,
        )
        disc = context.app_config.extraction.discovery
        return cls(
            project_discoverer=ProjectFileDiscoverer(scope=disc.project),
            dep_discoverer=DependencyFileDiscoverer(scope=disc.dependency),
        )

    def to_dict(self) -> dict:
        return {"type": "file_discovery"}


__all__ = ("FileDiscoveryStage",)
