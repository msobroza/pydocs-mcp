"""Ingestion-pipeline stages — one file per concrete stage.

Re-exports every stage class so existing imports
(``from pydocs_mcp.extraction.pipeline.stages import FileDiscoveryStage``)
keep working without each call site needing to learn the submodule path.

Module layout:

- :mod:`.base_stage` — :class:`IngestionStage` Protocol (re-exported here too)
- :mod:`.file_discovery` — :class:`FileDiscoveryStage`
- :mod:`.file_read` — :class:`FileReadStage`
- :mod:`.chunking` — :class:`ChunkingStage`
- :mod:`.reference_capture` — :class:`ReferenceCaptureStage` + ``_get_capture_config`` / ``_set_capture_config``
- :mod:`.flatten` — :class:`FlattenStage`
- :mod:`.assign_chunk_content_hash` — :class:`AssignChunkContentHashStage`
- :mod:`.embed_chunks` — :class:`EmbedChunksStage`
- :mod:`.content_hash` — :class:`ContentHashStage`
- :mod:`.package_build` — :class:`PackageBuildStage`

The split mirrors the SOLID rule from CLAUDE.md (Single Responsibility):
each file has one stage, one reason to change.
"""
from __future__ import annotations

from pydocs_mcp.extraction.pipeline.stages.assign_chunk_content_hash import (
    AssignChunkContentHashStage,
)
from pydocs_mcp.extraction.pipeline.stages.base_stage import IngestionStage
from pydocs_mcp.extraction.pipeline.stages.chunking import ChunkingStage
from pydocs_mcp.extraction.pipeline.stages.content_hash import ContentHashStage
from pydocs_mcp.extraction.pipeline.stages.embed_chunks import EmbedChunksStage
from pydocs_mcp.extraction.pipeline.stages.file_discovery import FileDiscoveryStage
from pydocs_mcp.extraction.pipeline.stages.file_read import FileReadStage
from pydocs_mcp.extraction.pipeline.stages.flatten import FlattenStage
from pydocs_mcp.extraction.pipeline.stages.package_build import PackageBuildStage
from pydocs_mcp.extraction.pipeline.stages.reference_capture import (
    ReferenceCaptureStage,
    _get_capture_config,
    _set_capture_config,
)

__all__ = (
    "AssignChunkContentHashStage",
    "ChunkingStage",
    "ContentHashStage",
    "EmbedChunksStage",
    "FileDiscoveryStage",
    "FileReadStage",
    "FlattenStage",
    "IngestionStage",
    "PackageBuildStage",
    "ReferenceCaptureStage",
    "_get_capture_config",
    "_set_capture_config",
)
