"""Shared base / contract for member extractors.

The :class:`MemberExtractor` Protocol lives in
:mod:`pydocs_mcp.application.protocols` (alongside the other
application-layer contracts). This module re-exports it so each
concrete-extractor file imports the contract from one obvious place.
"""
from __future__ import annotations

from pydocs_mcp.application.protocols import MemberExtractor

__all__ = ("MemberExtractor",)
