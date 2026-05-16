"""Application-layer Protocols — extraction + dependency resolution.

ChunkExtractor returns an :class:`ExtractionResult` so the extraction
pipeline can surface chunks, the ``DocumentNode`` forest, and the
:class:`Package` together as named fields (spec §5, AC #19).
Strategy-based implementations live in ``extraction/strategies/`` and
``extraction/pipeline/`` and depend only on these Protocols, keeping
``ProjectIndexer`` backend-agnostic. A dataclass is used (instead of a
``tuple[..., ..., ...]``) so adding future fields (e.g. extraction
stats) doesn't break every destructuring call site.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.models import Chunk, ModuleMember, Package


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Output of one :class:`ChunkExtractor` invocation.

    Carries flat chunks (FTS-bound), the document-tree forest (persisted
    to ``document_trees`` for lookup), and the package metadata in one
    immutable value so adding a future field (e.g. ``stats``) doesn't
    force every destructuring call site to change.
    """

    chunks: tuple[Chunk, ...]
    trees: tuple[DocumentNode, ...]
    package: Package


@runtime_checkable
class DependencyResolver(Protocol):
    async def resolve(self, project_dir: Path) -> tuple[str, ...]: ...


@runtime_checkable
class ChunkExtractor(Protocol):
    async def extract_from_project(
        self, project_dir: Path,
    ) -> ExtractionResult: ...

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> ExtractionResult: ...


@runtime_checkable
class MemberExtractor(Protocol):
    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[ModuleMember, ...]: ...

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[ModuleMember, ...]: ...
