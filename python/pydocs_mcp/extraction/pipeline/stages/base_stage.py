"""Shared base / contract for ingestion stages.

The :class:`IngestionStage` Protocol — every concrete stage in this
package implements it. The Protocol itself lives in
:mod:`pydocs_mcp.extraction.pipeline.ingestion` alongside
:class:`IngestionState` (the value object stages transform) because the
two are inextricably tied; this module re-exports the symbol so each
concrete-stage file imports its contract from one obvious place.
"""
from __future__ import annotations

from pydocs_mcp.extraction.pipeline.ingestion import IngestionStage

__all__ = ("IngestionStage",)
