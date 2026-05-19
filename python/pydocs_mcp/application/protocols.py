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

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.models import Chunk, ModuleMember, Package

if TYPE_CHECKING:
    # Imported only for typing — keeps the application layer from taking
    # a runtime dependency on storage value objects. ``NodeReference``
    # is emitted by a future ``ReferenceExtractionStage`` and persisted
    # by ``ReferenceStore`` (spec §4.2).
    from pydocs_mcp.storage.node_reference import NodeReference


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Output of one :class:`ChunkExtractor` invocation.

    Carries flat chunks (FTS-bound), the document-tree forest (persisted
    to ``document_trees`` for lookup), and the package metadata in one
    immutable value so adding a future field (e.g. ``stats``) doesn't
    force every destructuring call site to change.

    ``references`` defaults to an empty tuple so existing extractors that
    don't yet emit cross-node edges (spec §4.2, AC #21) keep working
    without modification — Open/Closed compliance for the extension.

    ``reference_aliases`` is the per-module alias table captured during
    ingestion (spec §7.2 Rule A). Carried alongside ``references`` so the
    resolver running inside ``IndexingService.reindex_package`` has both
    inputs — references for the unresolved edges, aliases for ``from X
    import Y as Z``-style rewrites.
    """

    chunks: tuple[Chunk, ...]
    trees: tuple[DocumentNode, ...]
    package: Package
    references: tuple[NodeReference, ...] = field(default=())
    reference_aliases: dict[str, dict[str, str]] = field(default_factory=dict)
    # Sub-PR #5d — per-class ``self.X`` attribute-type table built by
    # ``capture_self_attribute_types``. Drives the resolver's Rule 0
    # (self.X.Y inference); carried alongside ``reference_aliases``
    # because both feed the same resolver pass.
    class_attribute_types: dict[str, dict[str, str]] = field(default_factory=dict)


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
