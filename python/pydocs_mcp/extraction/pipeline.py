"""IngestionPipeline — write-side mirror of ``retrieval.CodeRetrieverPipeline``.

A 6-stage pipeline composed via decorator-registered stages. A SINGLE
pipeline handles both project and dependency modes — ``FileDiscoveryStage``
and ``PackageBuildStage`` branch on :attr:`IngestionState.target_kind`.
That keeps ``__main__.py`` / ``IndexProjectService`` from having two
near-duplicate write paths (spec §7.1).

Sub-PR #5b RESERVES the :attr:`IngestionState.references` field for
``NodeReference`` tuples emitted by a future ``ReferenceExtractionStage``;
sub-PR #5 stages do NOT populate it but the slot has to exist in this PR
so the frozen ``IngestionState`` contract is stable once stages start
reading/writing it in the next PR.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydocs_mcp.extraction.document_node import DocumentNode
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
    # Sub-PR #5b RESERVATION — populated by the future reference-extraction
    # stage. Kept here (not in extra_metadata) so it's a typed first-class
    # field the moment NodeReference ships.
    references:    tuple[Any, ...]                  = ()


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
