"""IngestionPipeline — write-side mirror of ``retrieval.CodeRetrieverPipeline``.

A 7-stage pipeline composed via decorator-registered stages
(``reference_capture`` sits between chunking and flatten). A SINGLE
pipeline handles both project and dependency modes — ``FileDiscoveryStage``
and ``PackageBuildStage`` branch on :attr:`IngestionState.target_kind`.
That keeps ``__main__.py`` / ``ProjectIndexer`` from having two
near-duplicate write paths (spec §7.1).

The reference-capture stage populates :attr:`IngestionState.references` +
:attr:`IngestionState.reference_aliases` via
:class:`~pydocs_mcp.extraction.pipeline.stages.ReferenceCaptureStage`,
which runs after chunking and before flatten. The resolver pass lives
later inside ``IndexingService.reindex_package`` so it has access to the
cross-package qname universe via ``uow.trees``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode
    from pydocs_mcp.models import Chunk, Package


class TargetKind(StrEnum):
    PROJECT    = "project"
    DEPENDENCY = "dependency"


@dataclass(frozen=True, slots=True)
class IngestionState:
    """Immutable state threaded through the ingestion pipeline.

    Stages return a NEW ``IngestionState`` (via ``dataclasses.replace``)
    rather than mutating in place — mirrors ``retrieval.PipelineState``
    so the same composition rules apply on the write side.

    ``target`` is either a ``Path`` (project directory) or a ``str``
    (PyPI distribution name) — the discriminator is :attr:`target_kind`,
    not Python's ``isinstance`` check, so both arms stay explicit.
    """

    target:        Path | str
    target_kind:   TargetKind
    package_name:  str                              = ""
    root:          Path                             = field(default_factory=lambda: Path("."))
    paths:         tuple[str, ...]                  = ()
    file_contents: tuple[tuple[str, str], ...]      = ()
    trees:         tuple["DocumentNode", ...]       = ()
    chunks:        tuple["Chunk", ...]              = ()
    content_hash:  str                              = ""
    package:       "Package | None"                 = None
    # Sub-PR #5b — populated by :class:`ReferenceCaptureStage`. Kept here
    # (not in extra_metadata) so it's a typed first-class field; the
    # resolver pass in :class:`IndexingService.reindex_package` reads
    # both ``references`` and ``reference_aliases`` together.
    references:    tuple[Any, ...]                  = ()
    # Sub-PR #5b — per-module alias table captured alongside references.
    # Forwarded to the resolver inside ``IndexingService.reindex_package``;
    # carried as a dict because alias semantics are sparse + per-module and
    # don't fit a flat tuple.
    reference_aliases: dict[str, dict[str, str]]    = field(default_factory=dict)
    # Sub-PR #5d — per-class ``self.X`` attribute types captured from
    # ``__init__`` bodies. Same shape as ``reference_aliases`` but keyed
    # by class qname instead of module qname; consumed by the resolver's
    # Rule 0 to rewrite ``self.X.Y`` → ``<type>.Y`` before Rule 5.
    class_attribute_types: dict[str, dict[str, str]] = field(default_factory=dict)


@runtime_checkable
class IngestionStage(Protocol):
    """One stage in the ingestion pipeline.

    Receives a state, returns a state. Mirrors the ``PipelineStage``
    shape from the retrieval side (spec §7.1, §7.5).
    """

    async def run(self, state: IngestionState) -> IngestionState: ...


@dataclass(frozen=True, slots=True)
class IngestionPipeline:
    """Composes a tuple of :class:`IngestionStage`\\s left-to-right.

    No routing built in — branching on ``target_kind`` happens INSIDE
    individual stages (e.g. ``FileDiscoveryStage`` calls the project or
    dependency discoverer based on ``state.target_kind``). Keeps the
    pipeline type one-dimensional: just a linear chain of stages.
    """

    stages: tuple[IngestionStage, ...]

    async def run(self, state: IngestionState) -> IngestionState:
        for stage in self.stages:
            state = await stage.run(state)
        return state
