"""Write-side ingestion pipeline machinery — :class:`IngestionPipeline`,
:class:`IngestionState`, the stage Protocol, the concrete stage
implementations, and :class:`PipelineChunkExtractor` (the Protocol
adapter that drives the pipeline for ``ProjectIndexer``).
"""
from pydocs_mcp.extraction.pipeline.chunk_extractor import PipelineChunkExtractor
from pydocs_mcp.extraction.pipeline.ingestion import (
    IngestionPipeline,
    IngestionStage,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages import (
    ChunkingStage,
    ContentHashStage,
    FileDiscoveryStage,
    FileReadStage,
    FlattenStage,
    PackageBuildStage,
)

__all__ = [
    "ChunkingStage",
    "ContentHashStage",
    "FileDiscoveryStage",
    "FileReadStage",
    "FlattenStage",
    "IngestionPipeline",
    "IngestionStage",
    "IngestionState",
    "PackageBuildStage",
    "PipelineChunkExtractor",
    "TargetKind",
]
