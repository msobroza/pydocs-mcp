"""Application-layer Protocols — extraction + dependency resolution.

ChunkExtractor returns a 3-tuple ``(chunks, trees, package)`` so the
extraction pipeline can surface the ``DocumentNode`` forest alongside the
flat chunks (spec §5, AC #19). Strategy-based implementations live in
``extraction/strategies/`` and ``extraction/pipeline/`` and depend only
on these Protocols, keeping ``ProjectIndexer`` backend-agnostic.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.models import Chunk, ModuleMember, Package


@runtime_checkable
class DependencyResolver(Protocol):
    async def resolve(self, project_dir: Path) -> tuple[str, ...]: ...


@runtime_checkable
class ChunkExtractor(Protocol):
    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]: ...

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]: ...


@runtime_checkable
class MemberExtractor(Protocol):
    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[ModuleMember, ...]: ...

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[ModuleMember, ...]: ...
