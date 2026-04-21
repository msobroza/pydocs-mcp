"""PipelineChunkExtractor — single :class:`IngestionPipeline` for both modes (spec §7.4).

Implements the sub-PR #4 :class:`~pydocs_mcp.application.protocols.ChunkExtractor`
3-tuple Protocol (``chunks, trees, package``). Both entry points delegate to
the SAME pipeline; differentiation happens via
:class:`~pydocs_mcp.extraction.pipeline.TargetKind` on the initial state, and
the stages themselves branch internally. That keeps
``IndexProjectService`` from having to pick between two extractor
implementations based on project-vs-dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.extraction.document_node import DocumentNode
from pydocs_mcp.extraction.pipeline import (
    IngestionPipeline,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.models import Chunk, Package


@dataclass(frozen=True, slots=True)
class PipelineChunkExtractor:
    """Implements sub-PR #4 ChunkExtractor Protocol via a single IngestionPipeline.

    The pipeline is passed in (dependency-injection) so tests can substitute a
    fake without touching YAML loading; production wiring constructs one via
    :func:`~pydocs_mcp.extraction.wiring.build_ingestion_pipeline`.
    """

    pipeline: IngestionPipeline

    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]:
        state = await self.pipeline.run(IngestionState(
            target=project_dir,
            target_kind=TargetKind.PROJECT,
            package_name="__project__",
        ))
        if state.package is None:
            raise RuntimeError(
                "ingestion pipeline did not populate state.package "
                "(missing package_build stage?)",
            )
        return state.chunks, state.trees, state.package

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]:
        state = await self.pipeline.run(IngestionState(
            target=dep_name,
            target_kind=TargetKind.DEPENDENCY,
            # Normalise once here (mirrors indexer.py / PackageBuildStage) so
            # chunks + trees share the canonical module prefix before the
            # package metadata is synthesized.
            package_name=normalize_package_name(dep_name),
        ))
        if state.package is None:
            raise RuntimeError(
                "ingestion pipeline did not populate state.package "
                "(missing package_build stage?)",
            )
        return state.chunks, state.trees, state.package


__all__ = ("PipelineChunkExtractor",)
