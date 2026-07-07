"""PipelineChunkExtractor — single :class:`IngestionPipeline` for both modes (spec §7.4).

Implements the sub-PR #4 :class:`~pydocs_mcp.application.protocols.ChunkExtractor`
Protocol — both entry points return an :class:`ExtractionResult`. Both
delegate to the SAME pipeline; differentiation happens via
:class:`~pydocs_mcp.extraction.pipeline.TargetKind` on the initial state, and
the stages themselves branch internally. That keeps
``ProjectIndexer`` from having to pick between two extractor
implementations based on project-vs-dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.application.protocols import ExtractionResult
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.extraction.pipeline.ingestion import (
    FileBundle,
    IngestionPipeline,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.models import PROJECT_PACKAGE_NAME


@dataclass(frozen=True, slots=True)
class PipelineChunkExtractor:
    """Implements sub-PR #4 ChunkExtractor Protocol via a single IngestionPipeline.

    The pipeline is passed in (dependency-injection) so tests can substitute a
    fake without touching YAML loading; production wiring constructs one via
    :func:`~pydocs_mcp.extraction.factories.build_ingestion_pipeline`.
    """

    pipeline: IngestionPipeline

    async def extract_from_project(
        self,
        project_dir: Path,
    ) -> ExtractionResult:
        return self._unwrap(
            await self.pipeline.run(
                IngestionState(
                    files=FileBundle(
                        target=project_dir,
                        target_kind=TargetKind.PROJECT,
                        package_name=PROJECT_PACKAGE_NAME,
                    ),
                )
            )
        )

    async def extract_from_dependency(
        self,
        dep_name: str,
    ) -> ExtractionResult:
        # Normalise once here (mirrors PackageBuildStage) so chunks +
        # trees share the canonical module prefix before the package
        # metadata is synthesized.
        pkg_name = normalize_package_name(dep_name)
        return self._unwrap(
            await self.pipeline.run(
                IngestionState(
                    files=FileBundle(
                        target=dep_name,
                        target_kind=TargetKind.DEPENDENCY,
                        package_name=pkg_name,
                    ),
                )
            )
        )

    @staticmethod
    def _unwrap(state: IngestionState) -> ExtractionResult:
        if state.package is None:
            raise RuntimeError(
                "ingestion pipeline did not populate state.package (missing package_build stage?)",
            )
        return ExtractionResult(
            chunks=state.chunks.chunks,
            trees=state.chunks.trees,
            package=state.package,
            references=state.refs.references,
            reference_aliases=state.refs.reference_aliases,
            class_attribute_types=state.refs.class_attribute_types,
            decisions=state.decisions,
            decision_structured=state.decision_structured,
        )


__all__ = ("PipelineChunkExtractor",)
