"""Extraction subpackage — strategy-based chunking + DocumentNode trees (sub-PR #5).

Structure:
- ``protocols.py``      — Protocol definitions (Chunker, ChunkExtractor, etc.)
- ``config.py``         — ExtractionConfig pydantic models + ``_EXCLUDED_DIRS`` policy
- ``factories.py``      — ``build_ingestion_pipeline`` / ``load_ingestion_pipeline``
- ``serialization.py``  — ``stage_registry``, ``chunker_registry`` (YAML wiring)
- ``model/``            — DocumentNode + NodeKind + tree helpers (domain types)
- ``pipeline/``         — IngestionPipeline + stages + PipelineChunkExtractor
- ``strategies/``       — chunkers, member extractors, discovery, dependencies

This module re-exports the public API surface from the subpackages so
external callers can keep doing ``from pydocs_mcp.extraction import X``.
For new code, prefer the subpackage form
(``from pydocs_mcp.extraction.model import DocumentNode``) which matches
the file layout 1:1.
"""

from pydocs_mcp.extraction.factories import (
    build_ingestion_pipeline,
    load_ingestion_pipeline,
)
from pydocs_mcp.extraction.model import (
    STRUCTURAL_ONLY_KINDS,
    DocumentNode,
    NodeKind,
    build_package_tree,
    flatten_to_chunks,
)
from pydocs_mcp.extraction.pipeline import (
    ChunkingStage,
    ContentHashStage,
    FileDiscoveryStage,
    FileReadStage,
    FlattenStage,
    IngestionPipeline,
    IngestionStage,
    IngestionState,
    PackageBuildStage,
    PipelineChunkExtractor,
    TargetKind,
)
from pydocs_mcp.extraction.serialization import chunker_registry, stage_registry
from pydocs_mcp.extraction.strategies import (
    AstMemberExtractor,
    AstPythonChunker,
    DependencyFileDiscoverer,
    HeadingMarkdownChunker,
    InspectMemberExtractor,
    NotebookChunker,
    ProjectFileDiscoverer,
    StaticDependencyResolver,
)

__all__ = [  # noqa: RUF022 — intentionally grouped by concept (chunkers / discovery / members / pipeline / stages / domain / consumers / registries), not alphabetical
    # Concrete chunkers
    "AstPythonChunker",
    "HeadingMarkdownChunker",
    "NotebookChunker",
    # Discovery
    "ProjectFileDiscoverer",
    "DependencyFileDiscoverer",
    # Members + deps
    "AstMemberExtractor",
    "InspectMemberExtractor",
    "StaticDependencyResolver",
    # Pipeline
    "IngestionPipeline",
    "IngestionState",
    "IngestionStage",
    "TargetKind",
    # Stages
    "FileDiscoveryStage",
    "FileReadStage",
    "ChunkingStage",
    "FlattenStage",
    "ContentHashStage",
    "PackageBuildStage",
    # Domain
    "DocumentNode",
    "NodeKind",
    "STRUCTURAL_ONLY_KINDS",
    # Assembly + consumers
    "PipelineChunkExtractor",
    "build_package_tree",
    "build_ingestion_pipeline",
    "load_ingestion_pipeline",
    "flatten_to_chunks",
    # Registries
    "stage_registry",
    "chunker_registry",
]
