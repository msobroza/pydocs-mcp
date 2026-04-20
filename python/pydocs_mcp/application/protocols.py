"""Application-layer Protocols — extraction + dependency resolution.

Sub-PR #4 ships thin adapters wrapping today's deps.py / indexer.py functions.
Sub-PR #5 replaces them with strategy-based implementations without touching
IndexProjectService or any other consumer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydocs_mcp.models import Chunk, ModuleMember, Package


@runtime_checkable
class DependencyResolver(Protocol):
    async def resolve(self, project_dir: Path) -> tuple[str, ...]: ...


@runtime_checkable
class ChunkExtractor(Protocol):
    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[tuple[Chunk, ...], Package]: ...

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[tuple[Chunk, ...], Package]: ...


@runtime_checkable
class MemberExtractor(Protocol):
    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[ModuleMember, ...]: ...

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[ModuleMember, ...]: ...
