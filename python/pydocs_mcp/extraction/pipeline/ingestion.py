"""IngestionPipeline — write-side mirror of ``retrieval.CodeRetrieverPipeline``.

A 7-stage pipeline composed via decorator-registered stages
(``reference_capture`` sits between chunking and flatten). A SINGLE
pipeline handles both project and dependency modes — ``FileDiscoveryStage``
and ``PackageBuildStage`` branch on :attr:`IngestionState.target_kind`.
That keeps ``__main__.py`` / ``ProjectIndexer`` from having two
near-duplicate write paths (spec §7.1).

:class:`IngestionState` is a thin envelope around three value-object
bundles:

* :class:`FileBundle` — discovery + file-read + content-hash outputs
  (``target`` / ``target_kind`` / ``package_name`` / ``root`` / ``paths``
  / ``file_contents`` / ``content_hash``).
* :class:`ChunkBundle` — chunking + flatten outputs (``trees`` and the
  flat ``chunks`` tuple).
* :class:`ReferenceBundle` — reference-capture outputs
  (``references`` / ``reference_aliases`` / ``class_attribute_types``).

The reference-capture stage populates ``state.refs`` via
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
    PROJECT = "project"
    DEPENDENCY = "dependency"


# Bundle value objects. Each holds one stage-group's slice of the
# pipeline state. Splitting :class:`IngestionState` into bundles keeps
# stage signatures honest about the slice they touch and stops the
# state from growing into a god object.


@dataclass(frozen=True, slots=True)
class FileBundle:
    """Discovery + file-read outputs: where to read from, what was read.

    Wraps the (``target``, ``target_kind``, ``package_name``, ``root``,
    ``paths``, ``file_contents``, ``content_hash``) tuple populated by
    :class:`FileDiscoveryStage`, :class:`FileReadStage`, and
    :class:`ContentHashStage`. Splitting the state into bundles keeps
    stage signatures honest about the slice they touch and stops
    :class:`IngestionState` from growing into a god object.
    """

    target: Path | str = field(default_factory=lambda: Path())
    target_kind: TargetKind = TargetKind.PROJECT
    package_name: str = ""
    root: Path = field(default_factory=lambda: Path())
    paths: tuple[str, ...] = ()
    file_contents: tuple[tuple[str, str], ...] = ()
    content_hash: str = ""


@dataclass(frozen=True, slots=True)
class ChunkBundle:
    """Chunking-stage outputs: the per-file trees + their flat chunk list.

    Populated by :class:`ChunkingStage` (``trees``) and
    :class:`FlattenStage` (``chunks``). See :class:`FileBundle` for the
    rationale behind the bundle split.
    """

    trees: tuple[DocumentNode, ...] = ()
    chunks: tuple[Chunk, ...] = ()


@dataclass(frozen=True, slots=True)
class ReferenceBundle:
    """Reference-capture-stage outputs: unresolved refs + alias tables.

    Populated by :class:`ReferenceCaptureStage`; consumed by the resolver
    pass inside :class:`IndexingService.reindex_package` (which has access
    to the cross-package qname universe via ``uow.trees``).

    * ``references`` — the unresolved tuple itself.
    * ``reference_aliases`` — per-module alias table; the resolver
      consumes it independently of whether IMPORTS rows survive the
      capture-config kinds filter (spec §5.3).
    * ``class_attribute_types`` — per-class ``self.X`` attribute-type
      table; used by the resolver's Rule 0 to rewrite ``self.X.Y`` →
      ``<type>.Y`` before Rule 5.
    """

    references: tuple[Any, ...] = ()
    reference_aliases: dict[str, dict[str, str]] = field(default_factory=dict)
    class_attribute_types: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IngestionState:
    """Immutable state threaded through the ingestion pipeline.

    Stages return a NEW ``IngestionState`` (via ``dataclasses.replace``)
    rather than mutating in place — mirrors ``retrieval.PipelineState``
    so the same composition rules apply on the write side.

    The state is a thin envelope around three value-object bundles plus
    two orthogonal scalars:

    * :attr:`files` — :class:`FileBundle` with discovery + file-read +
      content-hash outputs (carries ``target`` / ``target_kind`` /
      ``package_name`` so target-kind branching inside stages reads
      ``state.files.target_kind``; ``target`` is either a ``Path`` for
      project mode or a ``str`` distribution name for dependency mode).
    * :attr:`chunks` — :class:`ChunkBundle` with the per-file trees and
      the flat chunk list.
    * :attr:`refs` — :class:`ReferenceBundle` with the captured
      cross-node references + alias tables.

    Two scalars don't fit any bundle and stay top-level:

    * :attr:`package` — set by :class:`PackageBuildStage`; consumed by
      :class:`IndexingService.reindex_package`.
    * :attr:`existing_chunk_hashes` — populated by
      :class:`LoadExistingChunkHashesStage` and consumed by
      :class:`EmbedChunksStage` to skip re-embedding chunks whose
      pipeline-aware content_hash already lives in the DB
      (spec Decision 5). ``None`` distinguishes "stage didn't run"
      from ``{}`` ("ran and found nothing already cached").
    """

    files: FileBundle
    chunks: ChunkBundle = field(default_factory=ChunkBundle)
    refs: ReferenceBundle = field(default_factory=ReferenceBundle)
    package: Package | None = None
    existing_chunk_hashes: dict[str, int] | None = None
    # Merged mined decisions (spec §D8) — populated by CaptureDecisionsStage on
    # project targets, consumed by IndexingService.reindex_package (reconcile +
    # persist). Additive, default (), mirroring how ``refs`` travels the state:
    # dependency targets and any pipeline without the stage leave it empty.
    decisions: tuple[Any, ...] = ()


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
    dependency discoverer based on ``state.files.target_kind``). Keeps
    the pipeline type one-dimensional: just a linear chain of stages.
    """

    stages: tuple[IngestionStage, ...]

    async def run(self, state: IngestionState) -> IngestionState:
        for stage in self.stages:
            state = await stage.run(state)
        return state
