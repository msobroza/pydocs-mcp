"""Application-layer Protocols — extraction + dependency resolution.

Concrete implementations live in :mod:`pydocs_mcp.extraction`
(:class:`PipelineChunkExtractor` / :class:`AstMemberExtractor` /
:class:`InspectMemberExtractor` / :class:`StaticDependencyResolver`). The
service depends only on these Protocols; swapping extractors is a pure
adapter change.

``ChunkExtractor`` returns a 3-tuple ``(chunks, trees, package)`` (spec §5,
AC #19) so the pipeline can surface the ``DocumentNode`` forest alongside
the flat chunks.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydocs_mcp.extraction.document_node import DocumentNode
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
