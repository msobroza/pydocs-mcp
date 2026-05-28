"""Shared base / contract for chunkers.

The :class:`Chunker` Protocol lives in
:mod:`pydocs_mcp.extraction.protocols` (alongside the other
extraction-side contracts). This module re-exports it so each
concrete-chunker file imports the contract from one obvious place.
"""

from __future__ import annotations

from pydocs_mcp.extraction.protocols import Chunker

__all__ = ("Chunker",)
