"""Shared base / contract for retrieval-pipeline stages.

The :class:`PipelineStage` Protocol lives in
:mod:`pydocs_mcp.retrieval.protocols` alongside the other retrieval-side
abstractions. This module re-exports it so each concrete-stage file
imports the contract from one obvious place.
"""
from __future__ import annotations

from pydocs_mcp.retrieval.protocols import PipelineStage

__all__ = ("PipelineStage",)
