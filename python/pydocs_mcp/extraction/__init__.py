"""Extraction subpackage — strategy-based chunking + DocumentNode trees (sub-PR #5).

Public API:

- Protocols: :class:`Chunker`, :class:`ProjectFileDiscoverer`,
  :class:`DependencyFileDiscoverer` (imported lazily via ``protocols`` submodule).
- Domain: :class:`DocumentNode` + :class:`NodeKind` + :data:`STRUCTURAL_ONLY_KINDS`.
- Chunkers: :class:`AstPythonChunker`, :class:`HeadingMarkdownChunker`,
  :class:`NotebookChunker`.
- Discovery: concrete :class:`ProjectFileDiscoverer` / :class:`DependencyFileDiscoverer`
  wrappers (share the Protocol names — disambiguate via qualified import if needed).
- Member extractors: :class:`AstMemberExtractor`, :class:`InspectMemberExtractor`.
- Dependency resolution: :class:`StaticDependencyResolver`.
- Pipeline: :class:`IngestionPipeline`, :class:`IngestionState`,
  :class:`IngestionStage`, :class:`TargetKind`.
- Stages: :class:`FileDiscoveryStage`, :class:`FileReadStage`,
  :class:`ChunkingStage`, :class:`FlattenStage`, :class:`ContentHashStage`,
  :class:`PackageBuildStage`.
- Assembly: :func:`build_package_tree`, :func:`flatten_to_chunks`.
- Wiring: :func:`build_ingestion_pipeline`, :func:`load_ingestion_pipeline`.
- Consumer: :class:`PipelineChunkExtractor`.
- Registries: :data:`stage_registry`, :data:`chunker_registry`.
"""
from pydocs_mcp.extraction.chunk_extractor import PipelineChunkExtractor
from pydocs_mcp.extraction.chunkers import (
    AstPythonChunker,
    HeadingMarkdownChunker,
    NotebookChunker,
)
from pydocs_mcp.extraction.dependencies import StaticDependencyResolver
from pydocs_mcp.extraction.discovery import (
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)
from pydocs_mcp.extraction.document_node import (
    STRUCTURAL_ONLY_KINDS,
    DocumentNode,
    NodeKind,
)
from pydocs_mcp.extraction.members import AstMemberExtractor, InspectMemberExtractor
from pydocs_mcp.extraction.package_tree import build_package_tree
from pydocs_mcp.extraction.pipeline import (
    IngestionPipeline,
    IngestionStage,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.serialization import chunker_registry, stage_registry
from pydocs_mcp.extraction.stages import (
    ChunkingStage,
    ContentHashStage,
    FileDiscoveryStage,
    FileReadStage,
    FlattenStage,
    PackageBuildStage,
)
from pydocs_mcp.extraction.tree_flatten import flatten_to_chunks
from pydocs_mcp.extraction.wiring import (
    build_ingestion_pipeline,
    load_ingestion_pipeline,
)

__all__ = [
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
