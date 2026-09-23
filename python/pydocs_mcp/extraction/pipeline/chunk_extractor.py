"""PipelineChunkExtractor — single :class:`IngestionPipeline` for both modes (spec §7.4).

Implements the sub-PR #4 :class:`~pydocs_mcp.application.protocols.ChunkExtractor`
Protocol — every entry point returns an :class:`ExtractionResult`. All three
delegate to the SAME pipeline; differentiation happens via
:class:`~pydocs_mcp.extraction.pipeline.TargetKind` (and, for
``extract_from_paths``, ``FileBundle.explicit_paths``) on the initial state,
and the stages themselves branch internally. That keeps
``ProjectIndexer`` from having to pick between two extractor
implementations based on project-vs-dependency.
"""

from __future__ import annotations

from collections.abc import Sequence
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
        return await self._run_project(project_dir)

    async def extract_from_paths(
        self,
        project_root: Path,
        paths: Sequence[str],
    ) -> ExtractionResult:
        """Project-mode extraction of exactly ``paths`` (relative POSIX) under ``project_root``.

        The branch indexer's entry point for cache misses materialized into a
        scratch tree (spec §6.3 step 3, #309). No walk, and no decision mining:
        decisions per branch are P2 (O10), see ``CaptureDecisionsPipeline.run``.
        """
        if not paths:
            # An empty explicit list means "walk the root" to discovery, which
            # would extract every file of the scratch tree without a word.
            raise ValueError(
                f"extract_from_paths needs at least one path under {project_root}, got none"
            )
        return await self._run_project(project_root, tuple(paths))

    async def _run_project(
        self, root: Path, explicit_paths: tuple[str, ...] = ()
    ) -> ExtractionResult:
        """The one project-mode state both project entry points run (empty paths = walk)."""
        files = FileBundle(
            target=root,
            target_kind=TargetKind.PROJECT,
            package_name=PROJECT_PACKAGE_NAME,
            explicit_paths=explicit_paths,
        )
        return self._unwrap(await self.pipeline.run(IngestionState(files=files)))

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
            discovered_paths=tuple(state.files.paths),
            decisions=state.decisions,
            decision_structured=state.decision_structured,
        )


__all__ = ("PipelineChunkExtractor",)
